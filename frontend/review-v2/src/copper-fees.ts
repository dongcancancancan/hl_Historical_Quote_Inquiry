import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "./styles.css";
import CopperFeesApp from "./CopperFeesApp.vue";

createApp(CopperFeesApp).use(ElementPlus).mount("#app");
