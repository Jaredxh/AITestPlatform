<template>
  <n-modal
    v-model:show="visible"
    preset="dialog"
    title="上传需求文档"
    :show-icon="false"
    style="width: 520px"
    @after-leave="resetState"
  >
    <div class="mt-4">
      <n-upload
        :custom-request="handleUpload"
        :max="1"
        accept=".pdf,.docx,.doc,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        :file-list="fileList"
        @change="handleFileChange"
      >
        <n-upload-dragger>
          <div class="flex flex-col items-center gap-2 py-4">
            <span class="i-carbon-document-add text-4xl text-gray-400" />
            <n-text class="text-base">点击或拖拽文件到此区域</n-text>
            <n-text depth="3" class="text-sm">
              支持 .pdf、.docx、.doc 格式，单文件最大 20MB
            </n-text>
          </div>
        </n-upload-dragger>
      </n-upload>

      <n-alert v-if="uploadError" type="error" class="mt-3" closable @close="uploadError = ''">
        {{ uploadError }}
      </n-alert>
    </div>

    <template #action>
      <n-button @click="visible = false">关闭</n-button>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import {
  NModal,
  NUpload,
  NUploadDragger,
  NText,
  NButton,
  NAlert,
  useMessage,
} from "naive-ui";
import type { UploadFileInfo, UploadCustomRequestOptions } from "naive-ui";
import { uploadDocumentApi } from "@/services/requirements";
import { useProjectStore } from "@/stores/project";

const props = defineProps<{ show: boolean }>();
const emit = defineEmits<{
  (e: "update:show", value: boolean): void;
  (e: "success"): void;
}>();

const message = useMessage();
const projectStore = useProjectStore();

const visible = computed({
  get: () => props.show,
  set: (val) => emit("update:show", val),
});

const fileList = ref<UploadFileInfo[]>([]);
const uploadError = ref("");

function handleFileChange(data: { fileList: UploadFileInfo[] }) {
  fileList.value = data.fileList;
}

async function handleUpload({ file, onFinish, onError }: UploadCustomRequestOptions) {
  const projectId = projectStore.currentProjectId;
  if (!projectId) {
    uploadError.value = "请先选择一个项目";
    onError();
    return;
  }

  if (!file.file) {
    onError();
    return;
  }

  uploadError.value = "";
  try {
    const res = await uploadDocumentApi(projectId, file.file);
    if (res.success) {
      const data = res.data as typeof res.data & { parse_error?: string | null };
      if (data.parse_error) {
        message.warning(
          `文档「${data.filename}」已保存，但自动解析失败：${data.parse_error}。请在列表中点击该文档手动编辑文本，或重新另存为 .docx 上传。`,
          { duration: 8000 },
        );
      } else {
        message.success(`文档「${data.filename}」上传成功`);
      }
      onFinish();
      emit("success");
      visible.value = false;
    } else {
      uploadError.value = "上传失败，请重试";
      onError();
    }
  } catch (err: unknown) {
    const errorMsg =
      err instanceof Error ? err.message : "上传失败，请检查文件格式";
    uploadError.value = errorMsg;
    onError();
  }
}

function resetState() {
  fileList.value = [];
  uploadError.value = "";
}
</script>
