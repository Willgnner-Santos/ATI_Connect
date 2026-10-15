from helpers import api_post


def test_login_success(base_url, test_org):
    status, body = api_post(base_url, "/saas/auth/login", json={
        "email": test_org["adminEmail"],
        "password": test_org["adminPassword"],
    })
    assert status == 200
    auth = body["data"]["authorization"]
    assert auth["access_token"]
    assert auth["id_token"]
    assert auth["refresh_token"]
    assert body["data"]["user"]["email"] == test_org["adminEmail"]
    assert body["data"]["user"]["role"] == "admin"


def test_login_wrong_password(base_url, test_org):
    status, body = api_post(base_url, "/saas/auth/login", json={
        "email": test_org["adminEmail"],
        "password": "WrongPassword!1",
    })
    assert status == 401
    assert "message" in body


def test_login_missing_field(base_url):
    status, body = api_post(base_url, "/saas/auth/login", json={"email": "someone@example.com"})
    assert status == 400


def test_refresh_success(base_url, test_org):
    status, login_body = api_post(base_url, "/saas/auth/login", json={
        "email": test_org["adminEmail"],
        "password": test_org["adminPassword"],
    })
    assert status == 200
    refresh_token = login_body["data"]["authorization"]["refresh_token"]

    status, body = api_post(base_url, "/saas/auth/refresh", json={
        "refreshToken": refresh_token,
        "username": test_org["adminUserId"],
    })
    assert status == 200
    assert body["data"]["access_token"]
    assert body["data"]["id_token"]


def test_refresh_invalid_token(base_url, test_org):
    status, body = api_post(base_url, "/saas/auth/refresh", json={
        "refreshToken": "not-a-real-token",
        "username": test_org["adminUserId"],
    })
    assert status == 401


def test_logout_without_token(base_url):
    status, body = api_post(base_url, "/saas/auth/logout", json={})
    assert status == 401


def test_logout_with_valid_token(base_url, org_admin_token, test_org, test_tenant,
                                 run_id, user_factory):
    # global_sign_out revoga todos os tokens do usuário. Use uma conta descartável
    # para não invalidar o admin compartilhado pelas demais fixtures da sessão.
    email = f"qa-{run_id}-logout@example.com"
    password = "LogoutUserPass!1"
    status, _ = user_factory(
        org_admin_token,
        name="QA Logout User",
        email=email,
        organizationId=test_org["organizationId"],
        tenantId=test_tenant["tenantId"],
        role="user",
        password=password,
    )
    assert status == 201

    status, login_body = api_post(base_url, "/saas/auth/login", json={
        "email": email,
        "password": password,
    })
    assert status == 200
    access_token = login_body["data"]["authorization"]["access_token"]

    status, body = api_post(base_url, "/saas/auth/logout", token=access_token, json={})
    assert status == 200


def test_forgot_password_existing_email(base_url, test_org):
    status, body = api_post(base_url, "/saas/auth/forgot-password", json={
        "email": test_org["adminEmail"],
    })
    assert status == 200


def test_forgot_password_nonexistent_email_same_response(base_url):
    status, body = api_post(base_url, "/saas/auth/forgot-password", json={
        "email": "definitely-not-registered-qa@example.com",
    })
    assert status == 200


def test_reset_password_invalid_code(base_url, test_org):
    status, body = api_post(base_url, "/saas/auth/reset-password", json={
        "email": test_org["adminEmail"],
        "code": "000000",
        "newPassword": "NewPassword!1",
    })
    assert status == 400
