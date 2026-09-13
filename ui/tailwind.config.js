/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        "poly-blue": "#2d52f3",
        "hood-green": "#00c805",
        "hood-bg": "#000000",
        "glass-white": "rgba(255, 255, 255, 0.08)",
        "glass-border": "rgba(255, 255, 255, 0.15)",
      },
      boxShadow: {
        glass: "0 8px 32px 0 rgba(31, 38, 135, 0.37)",
        "glass-inset": "inset 0 1px 0 rgba(255,255,255,0.15)",
      },
      fontFamily: {
        sans: ['-apple-system','BlinkMacSystemFont','"SF Pro Display"','Inter','"Segoe UI"','Roboto','Helvetica','Arial','sans-serif','"Apple Color Emoji"'],
        mono: ['ui-monospace','SFMono-Regular','"SF Mono"','Menlo','monospace'],
      },
    },
  },
  plugins: [],
};
