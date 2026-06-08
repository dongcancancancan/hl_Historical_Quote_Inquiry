import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "./styles.css";
import BatchApp from "./BatchApp.vue";

createApp(BatchApp).use(ElementPlus).mount("#app");
