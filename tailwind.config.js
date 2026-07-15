/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./accounts/templates/**/*.html",
    "./tournament/templates/**/*.html",
    "./standings/templates/**/*.html",
  ],
  theme: {
    extend: {
      colors: {
        forest: {
          DEFAULT: "#24553B",
          light: "#2D6847",
          lighter: "#3A7A55",
        },
        brand: {
          DEFAULT: "#5ABF78",
          dark: "#419B5E",
          light: "#D8F2DF",
          faint: "#F3FBF3",
        },
        discord: {
          DEFAULT: "#5865F2",
          dark: "#4752C4",
          light: "#E8EAFF",
        },
        sage: "#F2FBF2",
      },
      fontFamily: {
        display: ['"Playfair Display"', "Georgia", "serif"],
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ['"Space Mono"', '"Courier New"', "monospace"],
      },
      animation: {
        "pulse-dot": "pulse-dot 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
      keyframes: {
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.3" },
        },
      },
      boxShadow: {
        card: "0 1px 3px 0 rgb(0 0 0 / 0.06), 0 1px 2px -1px rgb(0 0 0 / 0.04)",
        "card-hover":
          "0 4px 12px 0 rgb(0 0 0 / 0.08), 0 2px 4px -1px rgb(0 0 0 / 0.04)",
      },
    },
  },
  plugins: [],
};
