import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "./styles.css";
import PvcBomsApp from "./PvcBomsApp.vue";

createApp(PvcBomsApp).use(ElementPlus).mount("#app");
