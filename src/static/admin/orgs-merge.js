/**
 * orgs-merge.js — merge mode for the Organizations list table (#250).
 *
 * Thin consumer of the shared `window.createMergeMode` factory (merge-mode.js).
 * Mirrors people-merge.js; only the entity config differs. Loaded site-wide
 * from base.html <head>, AFTER merge-mode.js (defer preserves document order),
 * so the factory is defined before this runs (boost-safe — see #249).
 */
(function () {
  if (typeof window.createMergeMode !== 'function') return;
  window.createMergeMode({
    tableId: 'orgs-table',
    btnId: 'orgs-merge-btn',
    btnWrapId: 'orgs-merge-btn-wrap',
    barId: 'orgs-merge-bar',
    listRegionId: 'orgs-list-region',
    rowAttr: 'data-org-id',
    nounPlural: 'organizations',
    untitledLabel: '(unnamed)',
    buildPreviewUrl: function (winnerId, loserId) {
      // #255: open the org merge-preview modal in list context (winner pre-picked).
      return (
        '/admin/orgs/' +
        winnerId +
        '/merge-preview/' +
        loserId +
        '/?winner=' +
        winnerId +
        '&ctx=list'
      );
    },
  });
})();
