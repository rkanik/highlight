import { createTheme, type MantineColorsTuple } from "@mantine/core";

const ember: MantineColorsTuple = [
  "#fff8e6",
  "#fdeecf",
  "#f9db9f",
  "#f5c76a",
  "#f2b63e",
  "#f0ab22",
  "#efa511",
  "#d49004",
  "#bd7f00",
  "#a46c00",
];

const steel: MantineColorsTuple = [
  "#eef2f8",
  "#d9e0ea",
  "#b3bfd1",
  "#8b9cb7",
  "#6a7fa1",
  "#566d94",
  "#4b648f",
  "#3c547d",
  "#334a70",
  "#273f64",
];

export const theme = createTheme({
  primaryColor: "ember",
  primaryShade: { light: 6, dark: 5 },
  colors: { ember, steel },
  fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
  fontFamilyMonospace: '"IBM Plex Mono", Consolas, monospace',
  headings: {
    fontFamily: '"Chakra Petch", "Bahnschrift", sans-serif',
    fontWeight: "600",
  },
  defaultRadius: "md",
  cursorType: "pointer",
  components: {
    Button: { defaultProps: { fw: 600 } },
    Tooltip: { defaultProps: { openDelay: 250, withArrow: true } },
  },
});
