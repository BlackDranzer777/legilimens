/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    borderRadius: {
      none: '0',
      DEFAULT: '0',
      sm: '0',
      md: '0',
      lg: '0',
      xl: '0',
      full: '0',
    },
    extend: {
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Courier New"', 'monospace'],
      },
      colors: {
        accent: '#C8F400',
        'accent-dim': '#8aaa00',
        'bg-primary': '#090909',
        'bg-secondary': '#111111',
        'bg-card': '#141414',
        danger: '#FF3B3B',
        warning: '#FF9500',
        'border-dim': '#2a2a2a',
      },
    },
  },
  plugins: [],
}
