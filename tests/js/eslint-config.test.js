// @vitest-environment node
/**
 * Tests for eslint.config.js
 *
 * The test-file block spreads `vitest.environments.env.globals` and
 * `vitest.configs.recommended.rules` from @vitest/eslint-plugin. Spreading
 * `undefined` is legal, so a plugin release that reshapes either export would
 * drop every vitest/* rule while lint stays green. These tests resolve the
 * real config for a tests/js file and assert both survived. Issue #737.
 */
import { ESLint } from 'eslint';
import vitest from '@vitest/eslint-plugin';
import { describe, it, expect } from 'vitest';

const TEST_FILE = 'tests/js/eslint-config.test.js';

async function resolvedConfig() {
  const eslint = new ESLint();
  return eslint.calculateConfigForFile(TEST_FILE);
}

describe('eslint.config.js — tests/js block', () => {
  it('enables every recommended vitest/* rule', async () => {
    const config = await resolvedConfig();
    const recommended = Object.keys(vitest.configs.recommended.rules);
    expect(recommended.length).toBeGreaterThan(0);
    // calculateConfigForFile normalizes severity to a number; 0 = off
    const inactive = recommended.filter((rule) => !(config.rules[rule]?.[0] > 0));
    expect(inactive).toEqual([]);
  });

  it('declares the vitest test globals', async () => {
    const config = await resolvedConfig();
    const names = ['describe', 'it', 'expect', 'vi', 'beforeEach'];
    const missing = names.filter((name) => !(name in config.languageOptions.globals));
    expect(missing).toEqual([]);
  });
});
