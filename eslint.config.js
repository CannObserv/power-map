import js from '@eslint/js';
import vitest from 'eslint-plugin-vitest';
import prettier from 'eslint-config-prettier';
import globals from 'globals';

export default [
  // Production JS: browser IIFE scripts
  {
    files: ['src/static/admin/**/*.js'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: {
        ...globals.browser,
        htmx: 'readonly',
      },
    },
    rules: {
      ...js.configs.recommended.rules,
      'no-eval': 'warn',
    },
  },

  // Test files: Node + vitest + browser globals (happy-dom provides these at runtime)
  {
    files: ['tests/js/**/*.test.js'],
    plugins: { vitest },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.node,
        ...globals.browser,
        ...vitest.environments.env.globals,
      },
    },
    rules: {
      ...js.configs.recommended.rules,
      ...vitest.configs.recommended.rules,
      'no-eval': 'off', // intentional in IIFE test harness
    },
  },

  // Config files (ESM, Node context)
  {
    files: ['*.config.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: globals.node,
    },
    rules: {
      ...js.configs.recommended.rules,
    },
  },

  // Prettier last — disables formatting rules that conflict
  prettier,
];
