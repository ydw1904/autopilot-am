/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: "#F5F2EC",
        surface: "#FFFFFF",
        surface2: "#F8F6F1",
        border: "#E5E1D6",
        border2: "#CFC9BA",
        ink: "#0A1E3C",
        body: "#4E4B43",
        dim: "#8B877C",
        navy: "#05164D",
        gold: "#FFAD00",
        accent: {
          navy: "#05164D",
          gold: "#FFAD00",
          cyan: "#1D6FB8",
          amber: "#9E7600",
          green: "#1E7E46",
          purple: "#7C5CBF",
          red: "#C8102E",
        },
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "SF Pro Text", "Helvetica Neue", "sans-serif"],
        mono: ["JetBrains Mono", "SF Mono", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
