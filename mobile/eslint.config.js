const { defineConfig, globalIgnores } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  globalIgnores([
    '.expo/**',
    'dist/**',
    'lib/generated/**',
    'domain/translationRuntimeSources.generated.ts',
    'test-results/**',
  ]),
  expoConfig,
  {
    rules: {
      // These React Compiler diagnostics expose real legacy design debt, but
      // enabling them repo-wide would require lifecycle rewrites unrelated to
      // this quality gate. Keep the core Rules of Hooks active and introduce
      // compiler rules incrementally as affected components are refactored.
      'react-hooks/exhaustive-deps': 'error',
      'react-hooks/immutability': 'off',
      'react-hooks/preserve-manual-memoization': 'off',
      'react-hooks/purity': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/set-state-in-effect': 'off',
      '@typescript-eslint/array-type': 'error',
      '@typescript-eslint/no-unused-vars': 'error',
    },
  },
]);
