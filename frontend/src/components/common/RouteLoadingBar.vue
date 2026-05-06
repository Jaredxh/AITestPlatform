<template>
  <span class="hidden" />
</template>

<script setup lang="ts">
import { onMounted } from "vue";
import { useLoadingBar } from "naive-ui";
import { useRouter } from "vue-router";

const loadingBar = useLoadingBar();
const router = useRouter();

onMounted(() => {
  router.beforeEach((to, from, next) => {
    if (to.fullPath !== from.fullPath) {
      loadingBar.start();
    }
    next();
  });

  router.afterEach(() => {
    setTimeout(() => loadingBar.finish(), 50);
  });

  router.onError(() => {
    loadingBar.error();
  });
});
</script>

<style scoped>
.hidden {
  display: none;
}
</style>
