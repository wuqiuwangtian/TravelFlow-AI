// ESLint 扁平配置（ESLint 9+ / Next.js 16）
// eslint-config-next 直接导出数组，不是工厂函数
import next from "eslint-config-next";

const config = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      "test-results/**",
      "playwright-report/**",
    ],
  },
  ...next,
  {
    rules: {
      // React 19 的这条规则针对 React Compiler 的优化场景。
      // 本项目是纯客户端页面，在 effect 里拉取数据后 setState 是既有模式，
      // 改成框架级数据获取会引入额外依赖，收益不抵成本。
      // 因此降为 warning：保留提醒，但不阻断 CI。
      "react-hooks/set-state-in-effect": "warn",
    },
  },
];

export default config;
