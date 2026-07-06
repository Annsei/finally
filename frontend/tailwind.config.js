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
          up:      '#22c55e',
          down:    '#ef4444',
          amber:   '#f59e0b',
        },
      },
      keyframes: {
        flashUp: {
          '0%':   { backgroundColor: 'rgba(34, 197, 94, 0.25)' },
          '100%': { backgroundColor: 'transparent' },
        },
        flashDown: {
          '0%':   { backgroundColor: 'rgba(239, 68, 68, 0.25)' },
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
