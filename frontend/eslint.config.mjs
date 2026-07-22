import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "coverage/**",
    "next-env.d.ts",
  ]),
  // Jest mocks deliberately cross dynamic framework boundaries. Keep the
  // production TypeScript rule strict while allowing mock callbacks to use
  // the flexible signatures exposed by browser/chart libraries.
  {
    files: ["__tests__/**/*.{ts,tsx}", "__mocks__/**/*.{js,ts,tsx}"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  // Next's Jest helper is CommonJS-only in this project.
  {
    files: ["jest.config.js"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
  // React 19 advisory rules, downgraded here (NOT via CI-only flags) so local
  // `npm run lint` and the CI gate agree byte-for-byte. Existing effect/ref
  // patterns (e.g. src/components/ApiStatusProvider.tsx resetting failure
  // state on route change) have not been migrated yet; they stay visible as
  // warnings while behavior remains covered by Jest and the TypeScript gate.
  // TODO(frontend): migrate the flagged patterns and delete this override.
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
    },
  },
]);

export default eslintConfig;
