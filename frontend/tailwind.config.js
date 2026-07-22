/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx}',
    './src/components/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        // Local-first stack — no build-time font download (see _app.tsx note)
        mono: [
          '"JetBrains Mono"',
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      colors: {
        terminal: {
          bg:      '#0d1117',
          surface: '#1a1a2e',
          border:  '#30363d',
          text:    '#e6edf3',
          muted:   '#8b949e',
          accent:  '#ecad0a',
          blue:    '#209dd7',
          purple:  '#753991',
          // Direction colours resolve through CSS variables so the A-share
          // market can flip red-up/green-down at runtime (globals.css). US
          // defaults to the same green-up/red-down values as before.
          up:      'var(--color-up)',
          down:    'var(--color-down)',
          amber:   '#f59e0b',
        },
      },
      keyframes: {
        // Flash start colour is derived from the direction CSS variable so the
        // uptick/downtick highlight flips with the market. color-mix keeps the
        // same ~25% alpha the US market has always used.
        flashUp: {
          '0%':   { backgroundColor: 'color-mix(in srgb, var(--color-up) 25%, transparent)' },
          '100%': { backgroundColor: 'transparent' },
        },
        flashDown: {
          '0%':   { backgroundColor: 'color-mix(in srgb, var(--color-down) 25%, transparent)' },
          '100%': { backgroundColor: 'transparent' },
        },
      },
      animation: {
        'flash-up':   'flashUp 500ms ease-out forwards',
        'flash-down': 'flashDown 500ms ease-out forwards',
      },
    },
  },
  plugins: [],
};
