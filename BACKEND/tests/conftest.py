"""Fixtures de infraestrutura e dados para a suíte de testes de integração.

Sobe `sam local start-api` automaticamente para a sessão de testes, cria uma
organização/tenant/usuários dedicados de teste (prefixo `qa-{run_id}-`) via
chamadas HTTP reais contra a API local, e limpa tudo ao final (soft-delete via
API + hard-delete real via boto3 no DynamoDB/Cognito) para não deixar lixo no
ambiente sandbox.

Roda de ponta a ponta contra recursos reais da AWS (Cognito, DynamoDB, IoT,
EventBridge do stage `sandbox`) — não há mocking, ver docs/ops/local-testing.md.
"""
import os

# Remove variáveis de ambiente de outro perfil AWS antes de qualquer uso do
# boto3 neste processo (mesma causa raiz documentada em AGENTS.md:89) — não
# afeta o shell do usuário, só o processo do pytest.
for _var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
    os.environ.pop(_var, None)

import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import boto3
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import api_delete, api_get, api_post  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGION = "sa-east-1"
STAGE = "sandbox"
BASE_URL = "http://127.0.0.1:3001"
# .cmd não é executável nativo do WinAPI CreateProcess — precisa passar pelo cmd.exe.
SAM_CMD = ["cmd", "/c", r"C:\Program Files\Amazon\AWSSAMCLI\bin\sam.cmd"]

SUPERADMIN_EMAIL = "admin@plataforma-ati.com"
SUPERADMIN_PASSWORD = os.environ.get("ATI_TEST_SUPERADMIN_PASSWORD")

TABLE_ORGANIZATIONS = f"plataforma-ati-{STAGE}-organizations"
TABLE_TENANTS = f"plataforma-ati-{STAGE}-tenants"
TABLE_USERS = f"plataforma-ati-{STAGE}-tenant-users"
TABLE_ROLES = f"plataforma-ati-{STAGE}-organization-roles"
TABLE_EQUIPMENT = f"plataforma-ati-{STAGE}-tenant-equipment"


API_PORT = 3001


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _kill_process_tree(proc):
    """`proc` é o cmd.exe que embrulha o sam.cmd: terminate() mata só ele e deixa o
    `sam local start-api` neto vivo, segurando a porta. Rodadas seguintes falham no
    bind em silêncio e os testes passam a bater no servidor órfão (código e template
    antigos) — foi assim que um resultado verde virou mentira. taskkill /T mata a
    árvore inteira."""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def _test_password(run_id):
    return f"Qa{run_id}Test!1"


# Roles que o POST /saas/tenant semeia em TENANT#{tenantId}#ROLES (ver
# TENANT_DEFAULT_ROLES na layer). O soft-delete do tenant não as remove, então
# o teardown precisa apagá-las na mão para não deixar lixo no sandbox.
TENANT_ROLE_IDS = ("gestor", "tecnico", "user")


def _delete_tenant_roles(dynamodb, tenant_id):
    roles_table = dynamodb.Table(TABLE_ROLES)
    for role_id in TENANT_ROLE_IDS:
        roles_table.delete_item(Key={"pk": f"TENANT#{tenant_id}#ROLES", "sk": f"ROLE#{role_id}"})


@pytest.fixture(scope="session")
def run_id():
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="session")
def dynamodb():
    return boto3.resource("dynamodb", region_name=REGION)


@pytest.fixture(scope="session")
def equipment_table(dynamodb):
    """Acesso direto à tabela de equipamentos, para os testes que precisam
    plantar estado que só a telemetria escreveria (ex.: 'lastValues')."""
    return dynamodb.Table(TABLE_EQUIPMENT)


@pytest.fixture(scope="session")
def cognito_client():
    return boto3.client("cognito-idp", region_name=REGION)


@pytest.fixture(scope="session")
def cognito_user_pool_id(dynamodb):
    table = dynamodb.Table(TABLE_TENANTS)
    resp = table.get_item(Key={"pk": "CONFIG", "sk": "COGNITO#SAAS"})
    return resp["Item"]["userPoolId"]


@pytest.fixture(scope="session", autouse=True)
def base_url():
    """Sobe sam local start-api para a sessão inteira e devolve a base URL."""
    if _port_in_use(API_PORT):
        raise RuntimeError(
            f"Porta {API_PORT} já está em uso — provavelmente um `sam local start-api` "
            "órfão de uma rodada anterior. Os testes bateriam nele (código/template "
            "antigos) e o resultado não valeria nada. Feche-o antes de rodar: "
            f"netstat -ano | findstr :{API_PORT} e taskkill /F /T /PID <pid>."
        )

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate-local-template.py")],
        cwd=str(ROOT),
        check=True,
    )
    subprocess.run(
        SAM_CMD + ["build", "--template-file", "template.local.yaml", "--config-env", STAGE,
                   "--region", REGION, "--cached", "--parallel", "--use-container"],
        cwd=str(ROOT),
        check=True,
        timeout=900,
    )

    log_path = ROOT / "tests" / "_sam_local.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        SAM_CMD + ["local", "start-api", "--template-file", ".aws-sam/build/template.yaml",
                   "--config-env", STAGE, "--region", REGION, "--port", "3001",
                   "--warm-containers", "LAZY"],
        cwd=str(ROOT),
        env=os.environ.copy(),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    # O primeiro invoke pode precisar montar a imagem da layer local no Docker.
    # Uma chamada curta repetida dispara builds concorrentes e impede o warm-up.
    deadline = time.time() + 240
    ready = False
    while time.time() < deadline:
        try:
            remaining = max(1, deadline - time.time())
            requests.get(
                BASE_URL + "/saas/catalog/sensor-type",
                timeout=min(120, remaining),
            )
            ready = True
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
            time.sleep(2)

    if not ready:
        _kill_process_tree(proc)
        log_file.close()
        raise RuntimeError(f"sam local start-api não respondeu a tempo — ver {log_path}")

    yield BASE_URL

    _kill_process_tree(proc)
    log_file.close()
    shutil.rmtree(ROOT / ".aws-sam", ignore_errors=True)
    local_template = ROOT / "template.local.yaml"
    if local_template.exists():
        local_template.unlink()


def _login_superadmin(base_url):
    if not SUPERADMIN_PASSWORD:
        pytest.fail("Defina ATI_TEST_SUPERADMIN_PASSWORD para executar os testes E2E")
    status, body = api_post(base_url, "/saas/auth/login", json={
        "email": SUPERADMIN_EMAIL,
        "password": SUPERADMIN_PASSWORD,
    })
    assert status == 200, f"Falha no login do superadmin de teste: {body}"
    return body["data"]["authorization"]["access_token"]


@pytest.fixture(scope="session")
def superadmin_setup_token(base_url):
    return _login_superadmin(base_url)


@pytest.fixture
def superadmin_token(base_url):
    # Os tokens do sandbox expiram antes de uma suíte local completa em Docker.
    return _login_superadmin(base_url)


@pytest.fixture(scope="session")
def test_org(base_url, run_id, superadmin_setup_token, dynamodb, cognito_client, cognito_user_pool_id):
    admin_email = f"qa-{run_id}-admin@example.com"
    admin_password = _test_password(run_id)
    status, body = api_post(base_url, "/saas/organization", token=superadmin_setup_token, json={
        "name": f"qa-{run_id}-org",
        "email": f"qa-{run_id}-org@example.com",
        "cnpj": f"{run_id[0:2]}.{run_id[2:5]}.{run_id[5:8]}/0001-00".upper(),
        "admin": {
            "email": admin_email,
            "name": "QA Test Admin",
            "password": admin_password,
        },
    })
    assert status == 201, f"Falha ao criar organização de teste: {body}"
    org_id = body["data"]["organizationId"]
    admin_user_id = body["data"]["adminUserId"]

    yield {
        "organizationId": org_id,
        "adminEmail": admin_email,
        "adminPassword": admin_password,
        "adminUserId": admin_user_id,
    }

    api_delete(base_url, f"/saas/organization/{org_id}", token=_login_superadmin(base_url))

    dynamodb.Table(TABLE_ORGANIZATIONS).delete_item(Key={"pk": f"ORG#{org_id}", "sk": "METADATA"})
    roles_table = dynamodb.Table(TABLE_ROLES)
    for role_id in ("admin", "manager", "viewer"):
        roles_table.delete_item(Key={"pk": f"ORG#{org_id}#ROLES", "sk": f"ROLE#{role_id}"})
    dynamodb.Table(TABLE_USERS).delete_item(Key={"pk": f"ORG#{org_id}#USER", "sk": f"USER#{admin_user_id}"})
    try:
        cognito_client.admin_delete_user(UserPoolId=cognito_user_pool_id, Username=admin_user_id)
    except cognito_client.exceptions.UserNotFoundException:
        pass


@pytest.fixture(scope="session")
def org_admin_token(base_url, test_org):
    status, body = api_post(base_url, "/saas/auth/login", json={
        "email": test_org["adminEmail"],
        "password": test_org["adminPassword"],
    })
    assert status == 200, f"Falha no login do admin da org de teste: {body}"
    return body["data"]["authorization"]["access_token"]


@pytest.fixture(scope="session")
def test_tenant(base_url, run_id, test_org, org_admin_token, dynamodb, cognito_client, cognito_user_pool_id):
    tecnico_email = f"qa-{run_id}-tecnico@example.com"
    tecnico_password = _test_password(run_id)
    status, body = api_post(base_url, "/saas/tenant", token=org_admin_token, json={
        "organizationId": test_org["organizationId"],
        "name": f"qa-{run_id}-tenant",
        "cnpj": f"{run_id[6:8]}.{run_id[3:6]}.{run_id[0:3]}/0002-00".upper(),
        "user": {
            "email": tecnico_email,
            "name": "QA Test Tecnico",
            "password": tecnico_password,
            # Usuário de tenant resolve role apenas em TENANT#{tenantId}#ROLES,
            # então só as roles semeadas pelo tenant/new são válidas aqui.
            "role": "tecnico",
        },
    })
    assert status == 201, f"Falha ao criar tenant de teste: {body}"
    tenant_id = body["data"]["tenantId"]
    tecnico_user_id = body["data"]["userId"]

    yield {
        "tenantId": tenant_id,
        "organizationId": test_org["organizationId"],
        "tecnicoEmail": tecnico_email,
        "tecnicoPassword": tecnico_password,
        "tecnicoUserId": tecnico_user_id,
    }

    api_delete(base_url, f"/saas/tenant/{tenant_id}", token=org_admin_token)

    org_id = test_org["organizationId"]
    dynamodb.Table(TABLE_TENANTS).delete_item(Key={"pk": f"ORG#{org_id}#TENANTS", "sk": f"TENANT#{tenant_id}"})
    _delete_tenant_roles(dynamodb, tenant_id)
    dynamodb.Table(TABLE_USERS).delete_item(Key={"pk": f"ORG#{org_id}#USER", "sk": f"USER#{tecnico_user_id}"})
    try:
        cognito_client.admin_delete_user(UserPoolId=cognito_user_pool_id, Username=tecnico_user_id)
    except cognito_client.exceptions.UserNotFoundException:
        pass


@pytest.fixture(scope="session")
def tenant_tecnico_token(base_url, test_tenant):
    status, body = api_post(base_url, "/saas/auth/login", json={
        "email": test_tenant["tecnicoEmail"],
        "password": test_tenant["tecnicoPassword"],
    })
    assert status == 200, f"Falha no login do técnico de teste: {body}"
    return body["data"]["authorization"]["access_token"]


@pytest.fixture(scope="session")
def tenant_user(base_url, run_id, test_org, test_tenant, org_admin_token, dynamodb, cognito_client, cognito_user_pool_id):
    """Usuário de tenant com a role 'user' (só observa): usado para provar que o
    grant nega o que a role 'tecnico' pode ver/executar."""
    user_email = f"qa-{run_id}-user@example.com"
    user_password = _test_password(run_id)
    status, body = api_post(base_url, "/saas/user", token=org_admin_token, json={
        "name": "QA Test User",
        "email": user_email,
        "organizationId": test_org["organizationId"],
        "tenantId": test_tenant["tenantId"],
        "role": "user",
        "tenantRole": "user",
        "password": user_password,
    })
    assert status == 201, f"Falha ao criar usuário 'user' de teste: {body}"
    tenant_user_id = body["data"]["userId"]

    yield {"email": user_email, "password": user_password, "userId": tenant_user_id}

    dynamodb.Table(TABLE_USERS).delete_item(
        Key={"pk": f"ORG#{test_org['organizationId']}#USER", "sk": f"USER#{tenant_user_id}"}
    )
    try:
        cognito_client.admin_delete_user(UserPoolId=cognito_user_pool_id, Username=tenant_user_id)
    except cognito_client.exceptions.UserNotFoundException:
        pass


@pytest.fixture(scope="session")
def tenant_user_token(base_url, tenant_user):
    status, body = api_post(base_url, "/saas/auth/login", json={
        "email": tenant_user["email"],
        "password": tenant_user["password"],
    })
    assert status == 200, f"Falha no login do usuário 'user' de teste: {body}"
    return body["data"]["authorization"]["access_token"]



@pytest.fixture
def organization_factory(base_url, dynamodb, cognito_client, cognito_user_pool_id):
    """Cria organizações ad-hoc (fora da test_org de sessão) para testes que
    precisam de uma organização descartável própria (ex.: ciclo delete/reactivate,
    duplicidade de e-mail). Sempre faz hard-delete no teardown, independente do
    status em que a organização ficou durante o teste."""
    created = []

    def _create(token, admin_email=None, **payload):
        status, body = api_post(base_url, "/saas/organization", token=token, json=payload)
        if status == 201:
            created.append((body["data"]["organizationId"], body["data"].get("adminUserId")))
        return status, body

    yield _create

    org_table = dynamodb.Table(TABLE_ORGANIZATIONS)
    roles_table = dynamodb.Table(TABLE_ROLES)
    users_table = dynamodb.Table(TABLE_USERS)
    for org_id, admin_user_id in created:
        org_table.delete_item(Key={"pk": f"ORG#{org_id}", "sk": "METADATA"})
        for role_id in ("admin", "manager", "viewer"):
            roles_table.delete_item(Key={"pk": f"ORG#{org_id}#ROLES", "sk": f"ROLE#{role_id}"})
        if admin_user_id:
            users_table.delete_item(Key={"pk": f"ORG#{org_id}#USER", "sk": f"USER#{admin_user_id}"})
            try:
                cognito_client.admin_delete_user(UserPoolId=cognito_user_pool_id, Username=admin_user_id)
            except cognito_client.exceptions.UserNotFoundException:
                pass


@pytest.fixture
def tenant_factory(base_url, dynamodb, cognito_client, cognito_user_pool_id):
    """Cria tenants ad-hoc (fora da test_tenant de sessão) para testes de ciclo
    delete/reactivate. Sempre faz hard-delete no teardown."""
    created = []

    def _create(token, **payload):
        status, body = api_post(base_url, "/saas/tenant", token=token, json=payload)
        if status == 201:
            created.append((payload["organizationId"], body["data"]["tenantId"], body["data"].get("userId")))
        return status, body

    yield _create

    tenants_table = dynamodb.Table(TABLE_TENANTS)
    users_table = dynamodb.Table(TABLE_USERS)
    for organization_id, tenant_id, user_id in created:
        tenants_table.delete_item(Key={"pk": f"ORG#{organization_id}#TENANTS", "sk": f"TENANT#{tenant_id}"})
        _delete_tenant_roles(dynamodb, tenant_id)
        if user_id:
            users_table.delete_item(Key={"pk": f"ORG#{organization_id}#USER", "sk": f"USER#{user_id}"})
            try:
                cognito_client.admin_delete_user(UserPoolId=cognito_user_pool_id, Username=user_id)
            except cognito_client.exceptions.UserNotFoundException:
                pass


@pytest.fixture
def user_factory(base_url, dynamodb, cognito_client, cognito_user_pool_id):
    created = []

    def _create(token, **payload):
        status, body = api_post(base_url, "/saas/user", token=token, json=payload)
        if status == 201:
            created.append((payload["organizationId"], body["data"]["userId"]))
        return status, body

    yield _create

    users_table = dynamodb.Table(TABLE_USERS)
    for organization_id, user_id in created:
        users_table.delete_item(Key={"pk": f"ORG#{organization_id}#USER", "sk": f"USER#{user_id}"})
        try:
            cognito_client.admin_delete_user(UserPoolId=cognito_user_pool_id, Username=user_id)
        except cognito_client.exceptions.UserNotFoundException:
            pass


@pytest.fixture
def equipment_factory(base_url):
    created = []

    def _create(token, **overrides):
        payload = {
            "organizationId": overrides.pop("organizationId"),
            "tenantId": overrides.pop("tenantId"),
            "name": overrides.pop("name", "QA Test Equipment"),
            "equipmentType": overrides.pop("equipmentType", "sensor"),
            "model": overrides.pop("model", "qa-model"),
            "local": overrides.pop("local", "QA Lab"),
            "sensors": overrides.pop("sensors", {"temp1": {"sensorType": "analog"}}),
        }
        payload.update(overrides)
        status, body = api_post(base_url, "/saas/equipment", token=token, json=payload)
        if status == 201:
            created.append((token, body["data"]["equipmentId"]))
        return status, body

    yield _create

    for token, equipment_id in created:
        api_delete(base_url, f"/saas/equipment/{equipment_id}", token=token)


@pytest.fixture
def equipment_model_factory(base_url):
    created = []

    def _create(token, **payload):
        status, body = api_post(base_url, "/saas/catalog/equipment-model", token=token, json=payload)
        if status == 201:
            created.append((token, body["data"]["modelId"], payload.get("organizationId")))
        return status, body

    yield _create

    for token, model_id, organization_id in created:
        params = {"organizationId": organization_id} if organization_id else None
        api_delete(base_url, f"/saas/catalog/equipment-model/{model_id}", token=token, params=params)



@pytest.fixture
def vt_action_factory(base_url):
    created = []

    def _create(token, **payload):
        status, body = api_post(base_url, "/saas/virtual-technician", token=token, json=payload)
        if status == 201:
            created.append((token, body["data"]["actionId"]))
        return status, body

    yield _create

    for token, action_id in created:
        api_delete(base_url, f"/saas/virtual-technician/{action_id}", token=token)
