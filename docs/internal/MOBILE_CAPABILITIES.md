# 移动端功能迁移矩阵

本页面向实现与审查者，按当前路由源码盘点，防止只迁移少数按钮就声称手机功能完整。分类是**目标执行位置**，不是当前已支持离线或已完成移植。所有正式移动适配均未完成；本页不替代里程碑阶段状态。

## 分层与验收

| 目标类型 | 迁移要求 |
| --- | --- |
| LOCAL | 本地数据／文件与领域规则；无网络、电脑关闭时仍可完成，并保留历史、关联、幂等与权限边界。图片等内容以已在本机为前提。 |
| ONLINE | Android联网后独立调用可达服务；电脑关闭仍可用，失网清楚提示，不阻断本地操作。 |
| MIXED | 先拆开本地应用／撤销／草稿和可能的AI／地图调用；缓存与已有结果可本地处理，需要新结果时联网。不能让整个功能必须依赖电脑。 |
| DEVICE | 当前设备的配置／健康／服务能力；Key安全存储、网络与文件权限需平台适配，不默认随旅行同步。 |

## 按用户任务审查

- 旅行与版本：新建、编辑、分支、历史、归档／删除和偏好均需本地事务；主方案、删除记录与历史如何跨端合并另由同步协议定义。
- 旅中执行：进度、预约、预算、实付、回款、凭证、手写回顾不依赖模型；人工修改触发的地图重算拆成明确的在线步骤，未知值保持未知。
- 规划与AI：需求收集、推荐／生成、调整、AI回顾保留能力；本地确定性处理与远程任务解耦，手机密钥、流式恢复和结果应用各自验证。
- 照片与导出：已保存文件离线可读，上传／选择为本机文件导入，不需要电脑；相机、系统分享和下载是Android适配项，不能用浏览器截图替代。
- 高级工程：模板、项目与已有产物本地管理；运行涉及模型／地图／检索时手机独立联网执行，不能未经用户同意删除整个高级流程。
- 设置与运维：手机不是复制电脑.env；服务商配置、模型列表、健康、备份和设备配对各有平台边界。

## 路由覆盖清单

下表的方法与路径来自源码，分类需随迁移评审更新。`check_mobile_inventory.py`校验声明覆盖，不能证明分类正确或功能已实现。源码路径均相对 `backend/app/api/`。

| 源码符号 | 方法与完整路径 | 目标类型 |
| --- | --- | --- |
| `routes_chat.chat` | `POST /api/chat` | MIXED |
| `routes_chat.chat_stream` | `POST /api/chat/stream` | MIXED |
| `routes_export.export_recap` | `GET /api/versions/{version_id}/recap/export` | LOCAL |
| `routes_export.export_version` | `GET /api/versions/{version_id}/export` | LOCAL |
| `routes_itinerary.planning_change` | `GET /api/versions/{version_id}/planning-change` | LOCAL |
| `routes_itinerary.undo_planning_change` | `POST /api/versions/{version_id}/undo-plan` | LOCAL |
| `routes_itinerary.set_journey_status` | `PUT /api/versions/{version_id}/journey-status` | LOCAL |
| `routes_itinerary.recap_draft` | `POST /api/versions/{version_id}/recap/draft` | ONLINE |
| `routes_itinerary.get_recap` | `GET /api/versions/{version_id}/recap` | LOCAL |
| `routes_itinerary.save_recap` | `PUT /api/versions/{version_id}/recap` | LOCAL |
| `routes_itinerary.upload_travel_photo` | `POST /api/versions/{version_id}/travel-photos` | LOCAL |
| `routes_itinerary.get_travel_photo` | `GET /api/versions/{version_id}/travel-photos/{photo_id}` | LOCAL |
| `routes_itinerary.convert_location` | `POST /api/maps/convert-location` | ONLINE |
| `routes_itinerary.place_photos` | `GET /api/versions/{version_id}/activities/{activity_id}/photos` | ONLINE |
| `routes_itinerary.create_budget_item` | `POST /api/versions/{version_id}/budget-items` | LOCAL |
| `routes_itinerary.update_budget_item` | `PUT /api/versions/{version_id}/budget-items/{item_id}` | LOCAL |
| `routes_itinerary.delete_budget_item` | `DELETE /api/versions/{version_id}/budget-items/{item_id}` | LOCAL |
| `routes_itinerary.planner_preview` | `POST /api/versions/{version_id}/workflow/planner-preview` | MIXED |
| `routes_itinerary.current_planner_preview` | `GET /api/branches/{branch_id}/workflow/planner-preview` | LOCAL |
| `routes_itinerary.discard_planner_preview` | `DELETE /api/branches/{branch_id}/workflow/planner-preview/{request_id}` | LOCAL |
| `routes_itinerary.apply_planner_preview` | `POST /api/branches/{branch_id}/workflow/planner-preview/{request_id}/apply` | MIXED |
| `routes_itinerary.places` | `GET /api/maps/places` | ONLINE |
| `routes_itinerary.test_map` | `POST /api/system/test-map` | ONLINE |
| `routes_itinerary.organise` | `POST /api/versions/{version_id}/itinerary` | MIXED |
| `routes_itinerary.update_activity` | `PUT /api/versions/{version_id}/days/{number}/activities/{activity_id}` | MIXED |
| `routes_itinerary.recalculate` | `POST /api/versions/{version_id}/days/{number}/routes` | ONLINE |
| `routes_itinerary.day_map` | `GET /api/versions/{version_id}/days/{number}/map` | ONLINE |
| `routes_itinerary.update_progress` | `PATCH /api/versions/{version_id}/days/{number}/activities/{activity_id}/progress` | LOCAL |
| `routes_itinerary.create_expense` | `POST /api/versions/{version_id}/expenses` | LOCAL |
| `routes_itinerary.update_expense` | `PUT /api/versions/{version_id}/expenses/{expense_id}` | LOCAL |
| `routes_itinerary.delete_expense` | `DELETE /api/versions/{version_id}/expenses/{expense_id}` | LOCAL |
| `routes_itinerary.create_adjustment` | `POST /api/versions/{version_id}/expense-adjustments` | LOCAL |
| `routes_itinerary.update_adjustment` | `PUT /api/versions/{version_id}/expense-adjustments/{adjustment_id}` | LOCAL |
| `routes_itinerary.delete_adjustment` | `DELETE /api/versions/{version_id}/expense-adjustments/{adjustment_id}` | LOCAL |
| `routes_itinerary.update_booking` | `PATCH /api/versions/{version_id}/days/{number}/activities/{activity_id}/booking` | LOCAL |
| `routes_itinerary.replan_day` | `POST /api/versions/{version_id}/days/{number}/replan` | MIXED |
| `routes_itinerary.upload_receipt` | `POST /api/versions/{version_id}/receipts` | LOCAL |
| `routes_itinerary.get_receipt` | `GET /api/versions/{version_id}/receipts/{receipt_id}` | LOCAL |
| `routes_itinerary.recognise` | `POST /api/versions/{version_id}/receipts/{receipt_id}/recognise` | ONLINE |
| `routes_session.get_workflow_view` | `GET /api/branches/{branch_id}/workflow` | LOCAL |
| `routes_session.get_career_footprints` | `GET /api/travel-career/footprints` | LOCAL |
| `routes_session.get_career_memories` | `GET /api/travel-career/memories` | LOCAL |
| `routes_session.get_career_map` | `GET /api/travel-career/map` | ONLINE |
| `routes_session.get_travel_preferences` | `GET /api/travel-preferences` | LOCAL |
| `routes_session.save_travel_preferences` | `PUT /api/travel-preferences` | LOCAL |
| `routes_session.delete_travel_preferences` | `DELETE /api/travel-preferences` | LOCAL |
| `routes_session.get_travel_career` | `GET /api/travel-career` | LOCAL |
| `routes_session.recap_archive` | `GET /api/recap-archive` | LOCAL |
| `routes_session.list_sessions` | `GET /api/sessions` | LOCAL |
| `routes_session.create_session` | `POST /api/sessions` | LOCAL |
| `routes_session.get_session` | `GET /api/sessions/{session_id}` | LOCAL |
| `routes_session.update_session` | `PATCH /api/sessions/{session_id}` | LOCAL |
| `routes_session.delete_session` | `DELETE /api/sessions/{session_id}` | LOCAL |
| `routes_session.list_branches` | `GET /api/sessions/{session_id}/branches` | LOCAL |
| `routes_session.create_branch` | `POST /api/sessions/{session_id}/branches` | LOCAL |
| `routes_session.set_primary_branch` | `POST /api/branches/{branch_id}/primary` | LOCAL |
| `routes_session.delete_branch` | `DELETE /api/branches/{branch_id}` | LOCAL |
| `routes_session.list_versions` | `GET /api/branches/{branch_id}/versions` | LOCAL |
| `routes_session.get_version` | `GET /api/versions/{version_id}` | LOCAL |
| `routes_session.get_branch_messages` | `GET /api/branches/{branch_id}/messages` | LOCAL |
| `routes_studio.get_templates` | `GET /api/workflow/templates` | LOCAL |
| `routes_studio.get_user_templates` | `GET /api/workflow/user-templates` | LOCAL |
| `routes_studio.get_user_template` | `GET /api/workflow/user-templates/{template_id}` | LOCAL |
| `routes_studio.create_user_template` | `POST /api/workflow/user-templates` | LOCAL |
| `routes_studio.update_user_template` | `PUT /api/workflow/user-templates/{template_id}` | LOCAL |
| `routes_studio.delete_user_template` | `DELETE /api/workflow/user-templates/{template_id}` | LOCAL |
| `routes_studio.load_user_template` | `POST /api/branches/{branch_id}/studio/template-drafts/{template_id}` | LOCAL |
| `routes_studio.validate_project` | `POST /api/workflow/validate` | LOCAL |
| `routes_studio.get_project` | `GET /api/branches/{branch_id}/studio` | LOCAL |
| `routes_studio.save_project` | `PUT /api/branches/{branch_id}/studio` | LOCAL |
| `routes_studio.execute_project` | `POST /api/branches/{branch_id}/studio/runs` | MIXED |
| `routes_studio.stop_run` | `POST /api/branches/{branch_id}/studio/runs/{run_id}/stop` | LOCAL |
| `routes_studio.adoption_context` | `GET /api/branches/{branch_id}/studio/runs/{run_id}/nodes/{node_id}/adoption` | LOCAL |
| `routes_studio.preview_adoption` | `POST /api/branches/{branch_id}/studio/runs/{run_id}/nodes/{node_id}/adoption` | MIXED |
| `routes_studio.discard_adoption` | `DELETE /api/branches/{branch_id}/studio/runs/{run_id}/nodes/{node_id}/adoption/{request_id}` | LOCAL |
| `routes_studio.apply_adoption` | `POST /api/branches/{branch_id}/studio/runs/{run_id}/nodes/{node_id}/adoption/{request_id}/apply` | MIXED |
| `routes_system.health` | `GET /api/system/health` | DEVICE |
| `routes_system.config_check` | `GET /api/system/config` | DEVICE |
| `routes_system.providers` | `GET /api/system/providers` | DEVICE |
| `routes_system.test_llm` | `POST /api/system/test-llm` | ONLINE |
| `routes_system.test_search` | `POST /api/system/test-search` | ONLINE |
| `routes_system.read_settings` | `GET /api/system/settings` | DEVICE |
| `routes_system.update_settings` | `PUT /api/system/settings` | DEVICE |
| `routes_system.list_models` | `GET /api/system/models` | ONLINE |

声明共84项；以源码和检查器当前结果为准。Android实际能力、SDK与离线覆盖必须另给运行证据。

## 当前原型证据

账目只读汇总已完成Python参考样例与TS原型的14组口径对照，详见[里程碑证据](MOBILE_CONTINUITY_PLAN.md#p0账目领域口径核对2026-11-05)。它不是整个LOCAL类别或写入接口已迁移，也不改变上表正式适配未完成的状态。
