<template>
  <!--
    M1 占位：仅支持 SOFT / NONE 强度；HIGH 风险走 STRICT 时仍显示一个简化的
    "我已知晓"勾选 + 提示文本，但**不**强制输入挑战短语。M2 task 13.5 接入
    risk_level 真实字段后会替换为：
      - 必须输入 confirmation_payload.challenge_value（区分大小写）
      - 必须勾选 ack_label
      - 都满足才放开"确认执行"按钮
    本组件 props 已经按 M2 入参留好接口，M2 内部填实现即可，调用方无需改动。
  -->
  <div class="strict-dialog">
    <p class="strict-dialog__msg">
      {{ payload.message || "你即将执行高风险操作，请再次确认。" }}
    </p>
    <n-checkbox v-model:checked="acked">
      {{ payload.ack_label || "我已知晓相关风险，确认执行" }}
    </n-checkbox>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { NCheckbox } from "naive-ui";

const props = defineProps<{
  payload: {
    message?: string;
    challenge?: string;
    challenge_value?: string;
    ack_label?: string;
  };
}>();

const emit = defineEmits<{ (e: "ready", value: boolean): void }>();

const acked = ref(false);
watch(
  acked,
  (v) => emit("ready", v),
  { immediate: true },
);
// challenge_value 在 M1 暂不强制；M2 task 13.5 启用后这里需要再加一个文本输入
// + 比较逻辑。当前直接以 acked 作为 ready 信号给上层。
void props;
</script>

<style scoped>
.strict-dialog {
  background: color-mix(in srgb, var(--brand-warning, #f0a020) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--brand-warning, #f0a020) 40%, transparent);
  border-radius: 8px;
  padding: 8px 12px;
  margin-top: 8px;
}
.strict-dialog__msg {
  font-size: 12px;
  margin: 0 0 6px;
  color: var(--text-secondary);
}
</style>
