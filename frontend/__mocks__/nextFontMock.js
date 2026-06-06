// Mock for next/font/google — required for Jest (next/font doesn't work in jsdom)
// Source: nextjs.org/docs/pages/guides/testing/jest
module.exports = {
  JetBrains_Mono: () => ({
    variable: '--font-mono',
    className: 'mock-font',
  }),
};
