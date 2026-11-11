const { defineConfig, globalIgnores } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  globalIgnores([
    '.expo/**',
    'dist/**',
    'lib/generated/**',
    'src/lib/api/schema.gen.ts',
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
  {
    // New app (src/): no direct effects (docs/engineering/react-guidelines.md §3). The only
    // exception is src/lib/use-mount-effect.ts, which carries its own disable comment.
    files: ['src/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: 'react',
              importNames: ['useEffect', 'useLayoutEffect'],
              message: 'Use the replacements in react-guidelines §3 (Query, handlers, key, useSyncExternalStore, useFocusEffect, useMountEffect).',
            },
          ],
        },
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector: "MemberExpression[object.name='React'][property.name=/^use(Layout)?Effect$/]",
          message: 'Use the replacements in react-guidelines §3.',
        },
        {
          selector: "CallExpression[callee.object.name='Alert'][callee.property.name='alert']",
          message: 'Show errors in place (react-guidelines §5), not with Alert.alert.',
        },
      ],
      'react-hooks/set-state-in-effect': 'error',
      'react-hooks/refs': 'error',
      'react-hooks/purity': 'error',
      'react-hooks/immutability': 'error',
    },
  },
]);
