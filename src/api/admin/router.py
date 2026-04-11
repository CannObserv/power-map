"""Admin router — mounts all entity sub-routers."""

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from src.api.admin import activity as activity_module
from src.api.admin import dashboard as dashboard_module
from src.api.admin import entities as entities_module
from src.api.admin import imports as imports_module
from src.api.admin import orgs as orgs_module
from src.api.admin import orgs_acronyms as orgs_acronyms_module
from src.api.admin import orgs_addresses as orgs_addresses_module
from src.api.admin import orgs_contacts as orgs_contacts_module
from src.api.admin import orgs_identifiers as orgs_identifiers_module
from src.api.admin import orgs_links as orgs_links_module
from src.api.admin import orgs_names as orgs_names_module
from src.api.admin import orgs_roles as orgs_roles_module
from src.api.admin import people as people_module
from src.api.admin import people_addresses as people_addresses_module
from src.api.admin import people_assignments as people_assignments_module
from src.api.admin import people_contacts as people_contacts_module
from src.api.admin import people_identifiers as people_identifiers_module
from src.api.admin import people_links as people_links_module
from src.api.admin import people_names as people_names_module
from src.api.admin import role_assignments as role_assignments_module
from src.api.admin import roles as roles_module
from src.api.admin import roles_assignments_inline as roles_assignments_inline_module
from src.api.admin import roles_detail as roles_detail_module
from src.api.admin import settings as settings_module
from src.api.admin import settings_identifier_types as settings_identifier_types_module
from src.api.admin import settings_link_types as settings_link_types_module

templates = Jinja2Templates(directory="src/templates")
admin_router = APIRouter(prefix="/admin")

admin_router.include_router(dashboard_module.router)
admin_router.include_router(entities_module.router)
admin_router.include_router(imports_module.router)
admin_router.include_router(settings_module.router)
admin_router.include_router(settings_link_types_module.router)
admin_router.include_router(settings_identifier_types_module.router)
admin_router.include_router(activity_module.router)
admin_router.include_router(orgs_module.router)
admin_router.include_router(orgs_names_module.router)
admin_router.include_router(orgs_acronyms_module.router)
admin_router.include_router(orgs_addresses_module.router)
admin_router.include_router(orgs_contacts_module.router)
admin_router.include_router(orgs_links_module.router)
admin_router.include_router(orgs_identifiers_module.router)
admin_router.include_router(orgs_roles_module.router)
admin_router.include_router(people_module.router)
admin_router.include_router(people_names_module.router)
admin_router.include_router(people_contacts_module.router)
admin_router.include_router(people_addresses_module.router)
admin_router.include_router(people_links_module.router)
admin_router.include_router(people_identifiers_module.router)
admin_router.include_router(people_assignments_module.router)
admin_router.include_router(roles_module.router)
admin_router.include_router(roles_detail_module.router)
admin_router.include_router(roles_assignments_inline_module.router)
admin_router.include_router(role_assignments_module.router)
