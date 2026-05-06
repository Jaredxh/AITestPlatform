<template>
  <n-config-provider
    :theme="theme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-loading-bar-provider>
      <n-message-provider>
        <n-dialog-provider>
          <n-notification-provider>
            <router-view />
            <route-loading-bar />
          </n-notification-provider>
        </n-dialog-provider>
      </n-message-provider>
    </n-loading-bar-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed } from "vue";
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NNotificationProvider,
  NLoadingBarProvider,
  darkTheme,
  zhCN,
  dateZhCN,
} from "naive-ui";
import { useThemeStore } from "@/stores/theme";
import { lightThemeOverrides, darkThemeOverrides } from "@/theme/overrides";
import RouteLoadingBar from "@/components/common/RouteLoadingBar.vue";

const themeStore = useThemeStore();
const theme = computed(() => (themeStore.isDark ? darkTheme : null));
const themeOverrides = computed(() =>
  themeStore.isDark ? darkThemeOverrides : lightThemeOverrides,
);
</script>
