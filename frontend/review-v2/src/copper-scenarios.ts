import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "./styles.css";
import CopperScenariosApp from "./CopperScenariosApp.vue";

createApp(CopperScenariosApp).use(ElementPlus).mount("#app");
