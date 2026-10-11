# OBS-V2模式合同结构及规则

旧T01为T01-1，原字段保留并追加new_contract_id。新T01保存模式合同：contract_id、contract_name、project_name、building_name、contract_start_date、contract_end_date、payee_name、bank_name、bank_account_number、contract_type、file_received_date、effective_date、expiry_date、building_id。

合同名格式：项目名_楼栋名_收款人姓名_合同类型合同。先按项目、楼栋、开始、结束、类型五项键定位；同键多收款组合分别建ID，原行再按收款信息消歧，不取首条。合同ID采用NC-加UUID，生成后固定保存，后续优先复用登记值。

生效等于开始，失效等于结束。接收时间取付款底表第39列，无唯一来源时默认开始时间，多个候选有冲突时保留待核对；月份精度不补造日。

contract_link保存新旧对应，contract_building逐栋保存，多栋合同主表显示ID列表。contract_id_registry固定ID，new_contract_lineage记录依据，migration_issue记录疑点。原来源及变更日志保留，不重复累计。

当前结构入口为当前结构版本.json，指向版本化空模板及SQL。常用旧模板因Excel占用保留原样。实际数据版本入口在被Git忽略的数据表/当前版本.json。以后原合同查询使用T01-1，旧T01取数程序必须先适配，不直接重跑。

实际数据、ID登记和核对包均不上传Git。当前工作分支不自动合并main。
