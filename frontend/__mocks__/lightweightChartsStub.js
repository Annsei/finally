// CJS stub so Jest can resolve 'lightweight-charts' (pure ESM package).
// jest.mock('lightweight-charts', factory) in individual tests overrides this
// with test-specific mock behaviour.
module.exports = {
  createChart: () => ({
    addSeries: () => ({ update: () => {} }),
    remove: () => {},
    applyOptions: () => {},
  }),
  LineSeries: {},
};
