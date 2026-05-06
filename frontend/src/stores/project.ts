import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { getProjectsApi, getProjectDetailApi } from "@/services/projects";
import type { ProjectInfo, ProjectDetail } from "@/services/projects";

const STORAGE_KEY = "current_project_id";

export const useProjectStore = defineStore("project", () => {
  const projects = ref<ProjectInfo[]>([]);
  const currentProjectId = ref<string | null>(localStorage.getItem(STORAGE_KEY));
  const currentProjectDetail = ref<ProjectDetail | null>(null);
  const loading = ref(false);

  const currentProject = computed(() =>
    projects.value.find((p) => p.id === currentProjectId.value) ?? null,
  );

  async function fetchProjects() {
    loading.value = true;
    try {
      const res = await getProjectsApi({ page: 1, page_size: 100 });
      if (res.success) {
        projects.value = res.data.items;
        if (currentProjectId.value) {
          const exists = projects.value.some((p) => p.id === currentProjectId.value);
          if (!exists) {
            setCurrentProject(projects.value.length > 0 ? projects.value[0].id : null);
          }
        } else if (projects.value.length > 0) {
          setCurrentProject(projects.value[0].id);
        }
      }
    } finally {
      loading.value = false;
    }
  }

  function setCurrentProject(projectId: string | null) {
    currentProjectId.value = projectId;
    currentProjectDetail.value = null;
    if (projectId) {
      localStorage.setItem(STORAGE_KEY, projectId);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  }

  async function fetchCurrentDetail() {
    if (!currentProjectId.value) return null;
    try {
      const res = await getProjectDetailApi(currentProjectId.value);
      if (res.success) {
        currentProjectDetail.value = res.data;
        return res.data;
      }
    } catch {
      currentProjectDetail.value = null;
    }
    return null;
  }

  function addProjectToList(project: ProjectInfo) {
    const idx = projects.value.findIndex((p) => p.id === project.id);
    if (idx >= 0) {
      projects.value[idx] = project;
    } else {
      projects.value.unshift(project);
    }
  }

  function removeProjectFromList(projectId: string) {
    projects.value = projects.value.filter((p) => p.id !== projectId);
    if (currentProjectId.value === projectId) {
      setCurrentProject(projects.value.length > 0 ? projects.value[0].id : null);
    }
  }

  return {
    projects,
    currentProjectId,
    currentProject,
    currentProjectDetail,
    loading,
    fetchProjects,
    setCurrentProject,
    fetchCurrentDetail,
    addProjectToList,
    removeProjectFromList,
  };
});
