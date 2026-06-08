import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "./styles.css";
import PvcMaterialPricesApp from "./PvcMaterialPricesApp.vue";

createApp(PvcMaterialPricesApp).use(ElementPlus).mount("#app");
