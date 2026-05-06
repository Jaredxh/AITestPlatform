import { computed } from "vue";
import { useAuthStore } from "@/stores/auth";

export function usePermission() {
  const authStore = useAuthStore();

  const isAdmin = computed(
    () => authStore.user?.is_superuser || authStore.hasPermission("user:manage"),
  );

  function has(permission: string): boolean {
    return authStore.hasPermission(permission);
  }

  function hasAny(...permissions: string[]): boolean {
    return permissions.some((p) => authStore.hasPermission(p));
  }

  function hasAll(...permissions: string[]): boolean {
    return permissions.every((p) => authStore.hasPermission(p));
  }

  return { has, hasAny, hasAll, isAdmin };
}
