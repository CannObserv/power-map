/**
 * roles-merge.js — merge mode for the Roles list table (#251).
 *
 * Thin consumer of the shared `window.createMergeMode` factory (merge-mode.js),
 * mirroring orgs-merge.js / people-merge.js. The one difference: role merge is
 * **org-scoped** (route `/admin/orgs/{org}/roles/{winner}/merge/{loser}/`, unique
 * per `(organization_id, lower(title))`), so this consumer supplies a same-org
 * `canMerge` predicate — the Keep buttons only enable when the two selected
 * roles share an org. The shared org also feeds the merge URL.
 *
 * IDs are namespaced `roles-list-*` to stay distinct from the org-detail roles
 * table (`roles-table` / `roles-merge-*`, driven by the older role-merge.js);
 * reusing those IDs would make both scripts double-bind the same DOM.
 *
 * Loaded site-wide from base.html <head>, AFTER merge-mode.js (defer preserves
 * document order), so the factory is defined before this runs (boost-safe —
 * see #249/#250).
 */
(function () {
  if (typeof window.createMergeMode !== 'function') return;
  window.createMergeMode({
    tableId: 'roles-list-table',
    btnId: 'roles-list-merge-btn',
    btnWrapId: 'roles-list-merge-btn-wrap',
    barId: 'roles-list-merge-bar',
    listRegionId: 'roles-list-region',
    rowAttr: 'data-role-id',
    groupAttr: 'orgId', // data-org-id — the same-org key
    nounPlural: 'roles',
    untitledLabel: '(untitled)',
    canMerge: function (a, b) {
      return a.group && b.group && a.group === b.group;
    },
    cannotMergeLabel: 'Roles must be in the same organization to merge',
    buildMergeUrl: function (winnerId, loserId, winnerEntry) {
      return '/admin/orgs/' + winnerEntry.group + '/roles/' + winnerId + '/merge/' + loserId + '/';
    },
  });
})();
