/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#111412",
        graphite: "#202522",
        bone: "#F4F0E7",
        paper: "#FBF8EF",
        teal: "#1F7A75",
        coral: "#C94F31",
        amber: "#C59A2D",
        mist: "#DDE3DD",
      },
      boxShadow: {
        line: "inset 0 0 0 1px rgba(17,20,18,0.12)",
      },
      fontFamily: {
        sans: ["Avenir Next", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["SFMono-Regular", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
