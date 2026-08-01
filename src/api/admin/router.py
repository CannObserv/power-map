"""Admin router — mounts all entity sub-routers."""

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from src.api.admin import activity as activity_module
from src.api.admin import activity_requests as activity_requests_module
from src.api.admin import dashboard as dashboard_module
from src.api.admin import dup_badges as dup_badges_module
from src.api.admin import entities as entities_module
from src.api.admin import entity_event_citations as entity_event_citations_module
from src.api.admin import imports as imports_module
from src.api.admin import jurisdictions as jurisdictions_module
from src.api.admin import jurisdictions_addresses as jurisdictions_addresses_module
from src.api.admin import jurisdictions_affiliations as jurisdictions_affiliations_module
from src.api.admin import jurisdictions_citations as jurisdictions_citations_module
from src.api.admin import jurisdictions_contacts as jurisdictions_contacts_module
from src.api.admin import jurisdictions_identifiers as jurisdictions_identifiers_module
from src.api.admin import jurisdictions_links as jurisdictions_links_module
from src.api.admin import jurisdictions_relationships as jurisdictions_relationships_module
from src.api.admin import orgs as orgs_module
from src.api.admin import orgs_acronyms as orgs_acronyms_module
from src.api.admin import orgs_addresses as orgs_addresses_module
from src.api.admin import orgs_citations as orgs_citations_module
from src.api.admin import orgs_contacts as orgs_contacts_module
from src.api.admin import orgs_events as orgs_events_module
from src.api.admin import orgs_identifiers as orgs_identifiers_module
from src.api.admin import orgs_links as orgs_links_module
from src.api.admin import orgs_merge as orgs_merge_module
from src.api.admin import orgs_names as orgs_names_module
from src.api.admin import orgs_roles as orgs_roles_module
from src.api.admin import people as people_module
from src.api.admin import people_addresses as people_addresses_module
from src.api.admin import people_assignments as people_assignments_module
from src.api.admin import people_citations as people_citations_module
from src.api.admin import people_contacts as people_contacts_module
from src.api.admin import people_embeddings as people_embeddings_module
from src.api.admin import people_events as people_events_module
from src.api.admin import people_identifiers as people_identifiers_module
from src.api.admin import people_links as people_links_module
from src.api.admin import people_locale_script_search as people_locale_script_search_module
from src.api.admin import people_merge as people_merge_module
from src.api.admin import people_name_citations as people_name_citations_module
from src.api.admin import people_name_suggest as people_name_suggest_module
from src.api.admin import people_names as people_names_module
from src.api.admin import people_reading_target_search as people_reading_target_search_module
from src.api.admin import role_assignments as role_assignments_module
from src.api.admin import role_assignments_citations as role_assignments_citations_module
from src.api.admin import role_assignments_contacts as role_assignments_contacts_module
from src.api.admin import role_assignments_identifiers as role_assignments_identifiers_module
from src.api.admin import role_assignments_links as role_assignments_links_module
from src.api.admin import (
    role_assignments_relationships as role_assignments_relationships_module,
)
from src.api.admin import roles as roles_module
from src.api.admin import roles_assignments_inline as roles_assignments_inline_module
from src.api.admin import roles_citations as roles_citations_module
from src.api.admin import roles_contacts as roles_contacts_module
from src.api.admin import roles_detail as roles_detail_module
from src.api.admin import roles_links as roles_links_module
from src.api.admin import settings as settings_module
from src.api.admin import settings_api_keys as settings_api_keys_module
from src.api.admin import settings_identifier_types as settings_identifier_types_module
from src.api.admin import settings_link_types as settings_link_types_module

templates = Jinja2Templates(directory="src/templates")
admin_router = APIRouter(prefix="/admin")

admin_router.include_router(dup_badges_module.router)
admin_router.include_router(dashboard_module.router)
admin_router.include_router(entities_module.router)
admin_router.include_router(imports_module.router)
admin_router.include_router(settings_module.router)
admin_router.include_router(settings_api_keys_module.router)
admin_router.include_router(settings_link_types_module.router)
admin_router.include_router(settings_identifier_types_module.router)
admin_router.include_router(activity_requests_module.router)
admin_router.include_router(activity_module.router)
# Merge routers must precede their entity routers: FastAPI matches routes in
# registration order, and the entity routers carry /{id}/ wildcards that would
# otherwise swallow literal paths like /duplicates/.
admin_router.include_router(orgs_merge_module.router)
admin_router.include_router(orgs_module.router)
admin_router.include_router(orgs_names_module.router)
admin_router.include_router(orgs_acronyms_module.router)
admin_router.include_router(orgs_addresses_module.router)
admin_router.include_router(orgs_contacts_module.router)
admin_router.include_router(orgs_citations_module.router)
admin_router.include_router(orgs_links_module.router)
admin_router.include_router(orgs_identifiers_module.router)
admin_router.include_router(orgs_events_module.router)
admin_router.include_router(orgs_roles_module.router)
# Same ordering constraint as orgs above.
admin_router.include_router(people_merge_module.router)
# Locale/script typeahead must mount BEFORE people_module so its
# `/_locale_search` and `/_script_search` paths beat the `/{person_id}/`
# catch-all defined in people_module.
admin_router.include_router(people_locale_script_search_module.router)
# Reading-target typeahead is person-scoped (`/{person_id}/_reading_target_search`)
# so it can mount in any order, but pairing it with the locale/script
# typeahead module keeps the typeahead routes adjacent.
admin_router.include_router(people_reading_target_search_module.router)
admin_router.include_router(people_module.router)
admin_router.include_router(people_names_module.router)
# Suggest-only decomposition endpoint (#139). Mounted after
# people_names so the wildcard CRUD prefix already exists; the
# `/suggest-parts/` suffix doesn't conflict with any route there.
admin_router.include_router(people_name_suggest_module.router)
admin_router.include_router(people_contacts_module.router)
admin_router.include_router(people_citations_module.router)
admin_router.include_router(people_name_citations_module.router)
admin_router.include_router(entity_event_citations_module.router)
admin_router.include_router(people_addresses_module.router)
admin_router.include_router(people_links_module.router)
admin_router.include_router(people_identifiers_module.router)
admin_router.include_router(people_assignments_module.router)
admin_router.include_router(people_events_module.router)
admin_router.include_router(people_embeddings_module.router)
admin_router.include_router(jurisdictions_module.router)
admin_router.include_router(jurisdictions_contacts_module.router)
admin_router.include_router(jurisdictions_citations_module.router)
admin_router.include_router(jurisdictions_links_module.router)
admin_router.include_router(jurisdictions_identifiers_module.router)
admin_router.include_router(jurisdictions_addresses_module.router)
admin_router.include_router(jurisdictions_relationships_module.router)
admin_router.include_router(jurisdictions_affiliations_module.jurisdiction_router)
admin_router.include_router(jurisdictions_affiliations_module.org_router)
admin_router.include_router(roles_module.router)
admin_router.include_router(roles_detail_module.router)
admin_router.include_router(roles_contacts_module.router)
admin_router.include_router(roles_citations_module.router)
admin_router.include_router(roles_links_module.router)
admin_router.include_router(roles_assignments_inline_module.router)
admin_router.include_router(role_assignments_module.router)
admin_router.include_router(role_assignments_contacts_module.router)
admin_router.include_router(role_assignments_citations_module.router)
admin_router.include_router(role_assignments_links_module.router)
admin_router.include_router(role_assignments_identifiers_module.router)
admin_router.include_router(role_assignments_relationships_module.router)
