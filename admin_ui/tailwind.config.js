/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"]
      },
      colors: {
        glass: "rgba(255, 255, 255, 0.08)"
      },
      boxShadow: {
        glass: "0 20px 60px rgba(15, 15, 15, 0.35)"
      }
    }
  },
  plugins: []
};
