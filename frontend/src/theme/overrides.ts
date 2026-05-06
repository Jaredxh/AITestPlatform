import type { GlobalThemeOverrides } from "naive-ui";

const radius = {
  sm: "6px",
  md: "10px",
  lg: "14px",
};

const shared: GlobalThemeOverrides = {
  common: {
    fontFamily:
      'Inter, -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Helvetica, Arial, sans-serif',
    fontWeightStrong: "600",
    borderRadius: radius.md,
    borderRadiusSmall: radius.sm,
    heightSmall: "30px",
    heightMedium: "36px",
    heightLarge: "42px",
  },
  Card: {
    borderRadius: radius.lg,
    paddingMedium: "20px 22px",
    paddingSmall: "16px 18px",
  },
  Button: {
    borderRadiusMedium: radius.md,
    fontWeight: "500",
  },
  Input: {
    borderRadius: radius.md,
  },
  Select: {
    peers: {
      InternalSelection: {
        borderRadius: radius.md,
      },
    },
  },
  Tag: {
    borderRadius: radius.sm,
  },
  Menu: {
    itemHeight: "40px",
    borderRadius: radius.md,
  },
  DataTable: {
    borderRadius: radius.lg,
    thFontWeight: "600",
  },
  Dialog: {
    borderRadius: radius.lg,
  },
  Modal: {
    borderRadius: radius.lg,
  },
  Drawer: {
    borderRadius: radius.lg,
  },
  Message: {
    borderRadius: radius.md,
  },
  Notification: {
    borderRadius: radius.md,
  },
  LoadingBar: {
    height: "3px",
  },
};

export const lightThemeOverrides: GlobalThemeOverrides = {
  ...shared,
  common: {
    ...shared.common,
    primaryColor: "#4F46E5",
    primaryColorHover: "#4338CA",
    primaryColorPressed: "#3730A3",
    primaryColorSuppl: "#4F46E5",
    infoColor: "#0EA5E9",
    successColor: "#16A34A",
    warningColor: "#F59E0B",
    errorColor: "#EF4444",
    bodyColor: "#F5F7FB",
    cardColor: "#FFFFFF",
    modalColor: "#FFFFFF",
    popoverColor: "#FFFFFF",
    tableColor: "#FFFFFF",
    tableHeaderColor: "#F0F2F8",
    dividerColor: "rgba(15, 23, 42, 0.08)",
    borderColor: "rgba(15, 23, 42, 0.1)",
    textColorBase: "#0F172A",
    textColor1: "#0F172A",
    textColor2: "#334155",
    textColor3: "#94A3B8",
    placeholderColor: "#94A3B8",
    boxShadow1: "0 1px 2px rgba(15, 23, 42, 0.04), 0 1px 3px rgba(15, 23, 42, 0.06)",
    boxShadow2:
      "0 4px 8px -2px rgba(15, 23, 42, 0.06), 0 6px 16px -8px rgba(15, 23, 42, 0.08)",
    boxShadow3: "0 8px 16px -4px rgba(15, 23, 42, 0.08), 0 16px 32px -8px rgba(15, 23, 42, 0.12)",
  },
  LoadingBar: {
    ...shared.LoadingBar,
    colorLoading: "#4F46E5",
  },
  Layout: {
    color: "#F5F7FB",
    siderColor: "#FFFFFF",
    headerColor: "rgba(255, 255, 255, 0.85)",
    headerBorderColor: "rgba(15, 23, 42, 0.06)",
    siderBorderColor: "rgba(15, 23, 42, 0.06)",
  },
  Menu: {
    ...shared.Menu,
    itemColorActive: "rgba(79, 70, 229, 0.1)",
    itemColorActiveHover: "rgba(79, 70, 229, 0.14)",
    itemTextColorActive: "#4338CA",
    itemTextColorActiveHover: "#4338CA",
    itemIconColorActive: "#4338CA",
    itemIconColorActiveHover: "#4338CA",
    arrowColorActive: "#4338CA",
  },
};

export const darkThemeOverrides: GlobalThemeOverrides = {
  ...shared,
  common: {
    ...shared.common,
    primaryColor: "#818CF8",
    primaryColorHover: "#A5B4FC",
    primaryColorPressed: "#6366F1",
    primaryColorSuppl: "#818CF8",
    infoColor: "#38BDF8",
    successColor: "#22C55E",
    warningColor: "#FBBF24",
    errorColor: "#F87171",
    bodyColor: "#0B0D12",
    cardColor: "#161922",
    modalColor: "#1C2030",
    popoverColor: "#1C2030",
    tableColor: "#161922",
    tableHeaderColor: "#1C2030",
    dividerColor: "rgba(255, 255, 255, 0.08)",
    borderColor: "rgba(255, 255, 255, 0.1)",
    textColorBase: "#E2E8F0",
    textColor1: "#E2E8F0",
    textColor2: "#94A3B8",
    textColor3: "#64748B",
    placeholderColor: "#64748B",
    boxShadow1: "0 1px 2px rgba(0, 0, 0, 0.4)",
    boxShadow2: "0 6px 18px rgba(0, 0, 0, 0.4)",
    boxShadow3: "0 12px 32px rgba(0, 0, 0, 0.5)",
  },
  LoadingBar: {
    ...shared.LoadingBar,
    colorLoading: "#818CF8",
  },
  Layout: {
    color: "#0B0D12",
    siderColor: "#12141B",
    headerColor: "rgba(18, 20, 27, 0.72)",
    headerBorderColor: "rgba(255, 255, 255, 0.06)",
    siderBorderColor: "rgba(255, 255, 255, 0.06)",
  },
  Menu: {
    ...shared.Menu,
    itemColorActive: "rgba(129, 140, 248, 0.18)",
    itemColorActiveHover: "rgba(129, 140, 248, 0.24)",
    itemTextColorActive: "#A5B4FC",
    itemTextColorActiveHover: "#A5B4FC",
    itemIconColorActive: "#A5B4FC",
    itemIconColorActiveHover: "#A5B4FC",
    arrowColorActive: "#A5B4FC",
  },
};
