/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        primary: "#002046",
        "primary-container": "#1b365d",
        surface: "#f6fafe",
        "surface-container": "#eaeef2",
        "surface-container-low": "#f0f4f8",
        "surface-container-lowest": "#ffffff",
        "surface-container-high": "#e4e9ed",
        "surface-container-highest": "#dfe3e7",
        "on-surface": "#171c1f",
        "on-surface-variant": "#54647a",
        "outline-variant": "#c4c6cf",
        tertiary: "#002336",
        "tertiary-container": "#003a55",
        "tertiary-fixed": "#c9e6ff",
        error: "#ba1a1a",
        "error-container": "#ffdad6",
        success: "#047857",
        warning: "#b45309"
      },
      fontFamily: {
        headline: ["Manrope", "sans-serif"],
        body: ["Inter", "sans-serif"]
      },
      boxShadow: {
        ambient: "0 4px 40px rgba(23, 28, 31, 0.06)"
      }
    }
  },
  plugins: []
};
