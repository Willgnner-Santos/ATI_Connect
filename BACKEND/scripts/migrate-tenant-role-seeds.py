"""Add new global tenant roles while preserving legacy role records."""
import boto3
from datetime import datetime, timezone

REGION = "sa-east-1"
ROLES_TABLE = "plataforma-ati-sandbox-organization-roles"
TENANTS_TABLE = "plataforma-ati-sandbox-tenants"
ROLE_DEFINITIONS = {
    "client": {"roleName": "Cliente", "description": "Acesso operacional de cliente", "permissions": ["equipment:read", "dashboard:read", "report:read"], "isSystemRole": True},
    "technician": {"roleName": "Técnico", "description": "Acesso operacional de técnico", "permissions": ["equipment:read", "equipment:update", "dashboard:read", "report:read"], "isSystemRole": True},
}
db = boto3.resource("dynamodb", region_name=REGION)
tenants = db.Table(TENANTS_TABLE)
roles = db.Table(ROLES_TABLE)
response = tenants.scan(ProjectionExpression="tenantId, organizationId")
items = response.get("Items", [])
while response.get("LastEvaluatedKey"):
    response = tenants.scan(ProjectionExpression="tenantId, organizationId", ExclusiveStartKey=response["LastEvaluatedKey"])
    items.extend(response.get("Items", []))
created = 0
for tenant in items:
    tenant_id, org_id = tenant.get("tenantId"), tenant.get("organizationId")
    if not tenant_id or not org_id:
        continue
    for role_id, definition in ROLE_DEFINITIONS.items():
        key = {"pk": f"TENANT#{tenant_id}#ROLES", "sk": f"ROLE#{role_id}"}
        if roles.get_item(Key=key).get("Item"):
            continue
        roles.put_item(Item={**key, **definition, "roleId": role_id, "organizationId": org_id, "tenantId": tenant_id, "createdAt": datetime.now(timezone.utc).isoformat()}, ConditionExpression="attribute_not_exists(pk)")
        created += 1
print(f"Tenants verificados: {len(items)}; roles novas: {created}")
