/**
 * people-merge.js — merge mode for the People list table.
 *
 * Thin consumer of the shared `window.createMergeMode` factory (merge-mode.js,
 * #250). All behaviour — toggle button, checkbox selection, the sticky action
 * bar that swaps in for `.pagination--sticky`, and the boost-safe lifecycle
 * (#249) — lives in the factory; this file only supplies the People config.
 *
 * Loaded site-wide from base.html <head>, AFTER merge-mode.js (defer preserves
 * document order), so the factory is defined before this runs. hx-boost strips
 * <head> from boosted responses, so an extra_head-only script never ran on a
 * boosted nav — site-wide loading fixes that (#249).
 */
(function () {
  if (typeof window.createMergeMode !== 'function') return;
  window.createMergeMode({
    tableId: 'people-table',
    btnId: 'people-merge-btn',
    btnWrapId: 'people-merge-btn-wrap',
    barId: 'people-merge-bar',
    listRegionId: 'people-list-region',
    rowAttr: 'data-person-id',
    nounPlural: 'people',
    untitledLabel: '(unnamed)',
    buildMergeUrl: function (winnerId, loserId) {
      return '/admin/people/' + winnerId + '/merge/' + loserId + '/';
    },
  });
})();
