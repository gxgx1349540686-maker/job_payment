CREATE INDEX T04_period_span ON T04_period(building_id,from_month_index,to_month_index);

CREATE INDEX t05_building_month ON T05(building_id,month_id);

CREATE TABLE "T01"("contract_id" TEXT PRIMARY KEY,"contract_name" TEXT,"project" TEXT,"building" TEXT,"contract_start_date" TEXT,"contract_end_date" TEXT,"payee_name" TEXT,"bank_name" TEXT,"bank_account_number" TEXT,"contract_type" TEXT,"file_received_date" TEXT,"effective_date" TEXT,"expiry_date" TEXT,"building_id" TEXT);

CREATE TABLE "T01-1"("contract_number" TEXT,"contract_name" TEXT,"project" TEXT,"building" TEXT,"contract_start_date" TEXT,"contract_end_date" TEXT,"signed_date" TEXT,"effective_date" TEXT,"expiry_date" TEXT,"is_current" INTEGER,"data_status" TEXT,"source_row_id" TEXT,"fee_rule_version" TEXT,"contract_type" TEXT,"payee_name" TEXT,"bank_name" TEXT,"bank_account_ref" TEXT,"contract_profile_source_row_id" TEXT,"bank_account_number" TEXT,"bank_verification_status" TEXT,"new_contract_id" TEXT REFERENCES T01(contract_id));

CREATE TABLE "T02"("rule_id" TEXT,"contract_type" TEXT,"calculation_method" TEXT,"unit_fee_yuan" REAL,"room_count_basis" TEXT,"unit_fee_basis" TEXT,"review_status" TEXT,"confirmed_by" TEXT,"confirmed_at" TEXT,"result_location" TEXT,"rule_note" TEXT);

CREATE TABLE "T02-1"("contract_version_id" TEXT,"rule_version" TEXT,"fee_type" TEXT,"rate_basis_points" INTEGER,"contract_phase" TEXT,"source_start_date" TEXT,"source_end_date" TEXT,"proposed_start_date" TEXT,"company_rate" REAL,"owner_rate" REAL,"building_id" TEXT,"source_sheet" TEXT,"source_row" INTEGER,"source_row_id" TEXT,"review_status" TEXT,"execution_condition" TEXT,"contract_id" TEXT,"remark" TEXT);

CREATE TABLE "T03"("building_sk" TEXT,"building_id" TEXT UNIQUE,"project_id" TEXT,"project" TEXT,"building" TEXT,"effective_date" TEXT,"expiry_date" TEXT,"is_current" INTEGER,"data_status" TEXT,"source_row_id" TEXT,"created_at" TEXT);

CREATE TABLE T04_candidate(building_id TEXT REFERENCES building_scope(building_id),month_id TEXT,contract_id TEXT REFERENCES T01(contract_id),basis TEXT,PRIMARY KEY(building_id,month_id,contract_id));

CREATE TABLE T04_period(building_id TEXT REFERENCES building_scope(building_id),from_month_index INTEGER,to_month_index INTEGER,contract_id TEXT REFERENCES execution_basis(basis_id),candidate_count INTEGER,remark TEXT);

CREATE TABLE T05("building_bill_source_id" TEXT PRIMARY KEY,"match_key" TEXT,"occupied_resource_count" REAL,"rental_income" REAL,"compensation_income" REAL,"utility_retained_amount" REAL,"company_fee_amount" REAL,"building_share_income" REAL,"building_id" TEXT,"building_sk" TEXT,"contract_version_id" TEXT,"month_id" TEXT,"business_date" TEXT,"source_row_id" TEXT,"etl_batch_id" TEXT,"etl_time" TEXT,"data_status" TEXT);

CREATE TABLE building_alias(from_building_id TEXT,to_building_id TEXT,PRIMARY KEY(from_building_id,to_building_id));

CREATE TABLE building_month_coverage_check(building_id TEXT,project TEXT,building TEXT,month_id TEXT,basis_ids TEXT,execution_modes TEXT,basis_kind TEXT,review_status TEXT,PRIMARY KEY(building_id,month_id));

CREATE TABLE building_scope(building_id TEXT PRIMARY KEY,project TEXT,building TEXT,canonical_building_id TEXT);

CREATE TABLE change_log(obs_row INTEGER,field_name TEXT,old_json TEXT,new_json TEXT);

CREATE TABLE contract_building(contract_id TEXT REFERENCES T01(contract_id),building_id TEXT,source_rule TEXT,PRIMARY KEY(contract_id,building_id));

CREATE TABLE contract_id_redirect(retired_contract_id TEXT PRIMARY KEY,current_contract_id TEXT REFERENCES T01(contract_id),reason TEXT);

CREATE TABLE contract_id_registry(contract_id TEXT PRIMARY KEY REFERENCES T01(contract_id),identity_json TEXT);

CREATE TABLE contract_link(legacy_obs_row INTEGER,new_contract_id TEXT REFERENCES T01(contract_id),original_number TEXT,original_name TEXT,match_rule TEXT,PRIMARY KEY(legacy_obs_row,new_contract_id));

CREATE TABLE contract_use_scope(contract_id TEXT REFERENCES T01(contract_id),use_scope TEXT,note TEXT);

CREATE TABLE current_issue(table_name TEXT,record_ref TEXT,issue TEXT,note TEXT);

CREATE TABLE current_source_audit(source_kind TEXT,record_ref TEXT,details_json TEXT);

CREATE TABLE execution_basis(basis_id TEXT PRIMARY KEY,contract_id TEXT REFERENCES T01(contract_id),factual_basis_id TEXT REFERENCES factual_management_basis(basis_id),basis_kind TEXT NOT NULL,CHECK((contract_id IS NOT NULL AND factual_basis_id IS NULL AND basis_kind="新模式合同") OR (contract_id IS NULL AND factual_basis_id IS NOT NULL AND basis_kind="事实代管依据")));

CREATE TABLE execution_selection(building_id TEXT,from_month TEXT,to_month TEXT,contract_id TEXT REFERENCES T01(contract_id),basis TEXT,PRIMARY KEY(building_id,from_month,to_month));

CREATE TABLE factual_management_basis(basis_id TEXT PRIMARY KEY,canonical_building_id TEXT REFERENCES building_scope(building_id),basis_name TEXT,project TEXT,building TEXT,execution_mode TEXT,confirmation_basis TEXT);

CREATE TABLE field_description(table_name TEXT,column_order INTEGER,field_name TEXT,description TEXT,PRIMARY KEY(table_name,column_order));

CREATE TABLE historical_execution_reference(building_id TEXT,month_id TEXT,source_contract_id TEXT,new_contract_ids TEXT,reference_note TEXT,PRIMARY KEY(building_id,month_id));

CREATE TABLE import_batch(version TEXT PRIMARY KEY,source_workbook TEXT,source_sha256 TEXT,record_count INTEGER,scope TEXT);

CREATE TABLE month_calendar(month_index INTEGER PRIMARY KEY,month_id TEXT UNIQUE,month_start TEXT,month_end TEXT);

CREATE TABLE rate_contract_link(rate_source_row INTEGER,contract_id TEXT REFERENCES T01(contract_id),PRIMARY KEY(rate_source_row,contract_id));

CREATE TABLE record_lineage(obs_row INTEGER,source_file TEXT,source_sheet TEXT,source_row INTEGER,role TEXT,details_json TEXT);

CREATE TABLE source_archive(source_file TEXT,source_sheet TEXT,source_row INTEGER,raw_json TEXT,PRIMARY KEY(source_file,source_sheet,source_row));

CREATE TABLE t04_original_contract_link (
 contract_id TEXT NOT NULL REFERENCES T01(contract_id),original_number TEXT NOT NULL,
 original_name TEXT,signed_date TEXT,source_rows_json TEXT NOT NULL,
 PRIMARY KEY(contract_id,original_number));

CREATE TABLE t05_amount_control(month_id TEXT,source_file TEXT,source_sheet TEXT,amount_field TEXT,unit TEXT,source_rows INTEGER,null_rows INTEGER,negative_rows INTEGER,source_control_total_decimal TEXT,PRIMARY KEY(source_file,source_sheet,amount_field));

CREATE TABLE t05_building_id_resolution(source_id TEXT PRIMARY KEY REFERENCES T05(building_bill_source_id),original_building_id TEXT,resolved_building_id TEXT REFERENCES building_scope(building_id),evidence_json TEXT);

CREATE TABLE t05_current_execution_link(source_id TEXT REFERENCES T05(building_bill_source_id),execution_basis_id TEXT REFERENCES execution_basis(basis_id),execution_mode TEXT,review_status TEXT,PRIMARY KEY(source_id,execution_basis_id));

CREATE TABLE t05_source_lineage(source_id TEXT PRIMARY KEY REFERENCES T05(building_bill_source_id),source_file TEXT,source_sheet TEXT,source_row INTEGER,source_sha256 TEXT,source_columns_json TEXT,raw_values_json TEXT,project TEXT,building TEXT,source_note TEXT);

CREATE TABLE t05_source_manifest(month_id TEXT,source_file TEXT PRIMARY KEY,source_sheet TEXT,header_row INTEGER,sha256 TEXT,valid_rows INTEGER,source_columns_json TEXT);

CREATE TABLE t05_source_row_audit(source_file TEXT,source_sheet TEXT,source_row INTEGER,row_kind TEXT,raw_json TEXT,PRIMARY KEY(source_file,source_sheet,source_row));

CREATE TABLE version_change_log(version TEXT,table_name TEXT,record_id TEXT,field_name TEXT,old_json TEXT,new_json TEXT,reason TEXT);

CREATE VIEW T04 AS SELECT x.contract_id,x.contract_name,l.original_number AS contract_number,
l.original_name AS original_contract_name,x.project,x.building,x.building_id,
x.contract_start_date,x.contract_end_date,l.signed_date,x.effective_date,x.month_id,
x.remark AS remark,x.execution_mode
FROM T04_execution x LEFT JOIN t04_original_contract_link l ON l.contract_id=x.contract_id;

CREATE VIEW T04_execution AS
WITH original AS (
 SELECT new_contract_id,
 group_concat(DISTINCT contract_number) AS contract_numbers,
 group_concat(DISTINCT contract_name) AS original_contract_names,
 group_concat(DISTINCT signed_date) AS signed_dates,
 count(DISTINCT contract_number) AS original_number_count
 FROM "T01-1" GROUP BY new_contract_id
)
SELECT p.contract_id,coalesce(n.contract_name,f.basis_name) AS contract_name,o.contract_numbers AS contract_number,o.original_contract_names AS original_contract_name,
b.project,b.building,p.building_id,n.contract_start_date,n.contract_end_date,o.signed_dates AS signed_date,n.effective_date,m.month_id,
p.remark
|| CASE WHEN o.original_number_count>1 THEN '；多个原万村编号对应同一新模式合同，不计为多份新模式合同' ELSE '' END
|| CASE WHEN p.candidate_count>1 AND coalesce(e.new_contract_ids,'')<>'' THEN '；V16已确认执行参考：'||e.new_contract_ids||'（原批次结论保留，当前不同新模式合同仍待核对）' ELSE '' END
|| CASE WHEN p.contract_id IS NULL AND EXISTS(SELECT 1 FROM contract_building cb JOIN contract_use_scope u ON u.contract_id=cb.contract_id WHERE cb.building_id=p.building_id) THEN '；存在仅供特殊付款合同，不能套入普通月账' ELSE '' END AS remark,
CASE WHEN f.basis_id IS NOT NULL THEN f.execution_mode
 WHEN n.contract_id IS NULL THEN NULL
 WHEN m.month_end<n.contract_start_date OR m.month_start>n.contract_end_date THEN '事实'||n.contract_type
 ELSE n.contract_type END AS execution_mode
FROM month_calendar m JOIN T04_period p ON m.month_index BETWEEN p.from_month_index AND p.to_month_index
JOIN building_scope b ON b.building_id=p.building_id
LEFT JOIN T01 n ON n.contract_id=p.contract_id
LEFT JOIN factual_management_basis f ON f.basis_id=p.contract_id
LEFT JOIN original o ON o.new_contract_id=p.contract_id
LEFT JOIN historical_execution_reference e ON e.building_id=b.canonical_building_id AND e.month_id=m.month_id;

CREATE VIEW "业务_T05楼栋月份实收基准" AS
SELECT x.building_id,b.project,b.building,x.month_id,min(x.building_bill_source_id) AS representative_source_id,count(*) AS source_count,
count(DISTINCT json_array(x.occupied_resource_count,x.rental_income,x.compensation_income,x.utility_retained_amount,x.company_fee_amount,x.building_share_income)) AS value_version_count,
CASE WHEN count(DISTINCT x.occupied_resource_count)=1 THEN max(x.occupied_resource_count) END AS occupied_resource_count,count(DISTINCT x.occupied_resource_count) AS occupied_resource_count_candidate_count,
CASE WHEN count(DISTINCT x.rental_income)=1 THEN max(x.rental_income) END AS rental_income,count(DISTINCT x.rental_income) AS rental_income_candidate_count,
CASE WHEN count(DISTINCT x.compensation_income)=1 THEN max(x.compensation_income) END AS compensation_income,count(DISTINCT x.compensation_income) AS compensation_income_candidate_count,
CASE WHEN count(DISTINCT x.utility_retained_amount)=1 THEN max(x.utility_retained_amount) END AS utility_retained_amount,count(DISTINCT x.utility_retained_amount) AS utility_retained_amount_candidate_count,
CASE WHEN count(DISTINCT x.company_fee_amount)=1 THEN max(x.company_fee_amount) END AS source_company_fee_amount,count(DISTINCT x.company_fee_amount) AS company_fee_amount_candidate_count,
CASE WHEN count(DISTINCT x.building_share_income)=1 THEN max(x.building_share_income) END AS source_building_share_income,count(DISTINCT x.building_share_income) AS building_share_income_candidate_count,
CASE WHEN count(DISTINCT json_array(x.occupied_resource_count,x.rental_income,x.compensation_income,x.utility_retained_amount,x.company_fee_amount,x.building_share_income))>1 THEN '源科目有差异，各科目独立展示一致的非空值，冲突或全空字段留NULL' WHEN count(*)>1 THEN '相同源科目去重展示；NULL不等于0' ELSE '源基准待核对；NULL不等于0' END AS review_status
FROM T05 x JOIN building_scope b ON b.building_id=x.building_id GROUP BY x.building_id,x.month_id;

CREATE VIEW 业务_合同月执行 AS SELECT building_id,month_id,
count(DISTINCT contract_id) AS execution_basis_count,
CASE WHEN count(DISTINCT contract_id)=1 THEN max(contract_id) ELSE NULL END AS contract_id,
CASE WHEN count(DISTINCT contract_id)=1 THEN '单一执行依据' ELSE '多份新模式合同待核对' END AS review_status
FROM T04_execution GROUP BY building_id,month_id;