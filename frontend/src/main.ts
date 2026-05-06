import { createApp } from "vue";
import { createPinia } from "pinia";
import router from "./router";
import App from "./App.vue";
import "virtual:uno.css";
import "@/assets/styles/tokens.css";
import "@/assets/styles/global.css";
import "@/assets/styles/transitions.css";

const app = createApp(App);

app.use(createPinia());
app.use(router);
app.mount("#app");
