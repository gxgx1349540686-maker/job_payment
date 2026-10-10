CREATE TABLE "T01"("contract_id" TEXT PRIMARY KEY,"contract_name" TEXT,"project" TEXT,"building" TEXT,"contract_start_date" TEXT,"contract_end_date" TEXT,"payee_name" TEXT,"bank_name" TEXT,"bank_account_number" TEXT,"contract_type" TEXT,"file_received_date" TEXT,"effective_date" TEXT,"expiry_date" TEXT,"building_id" TEXT);
CREATE TABLE "T01-1"("contract_number" TEXT,"contract_name" TEXT,"project" TEXT,"building" TEXT,"contract_start_date" TEXT,"contract_end_date" TEXT,"signed_date" TEXT,"effective_date" TEXT,"expiry_date" TEXT,"is_current" INTEGER,"data_status" TEXT,"source_row_id" TEXT,"fee_rule_version" TEXT,"contract_type" TEXT,"payee_name" TEXT,"bank_name" TEXT,"bank_account_ref" TEXT,"contract_profile_source_row_id" TEXT,"bank_account_number" TEXT,"bank_verification_status" TEXT,"new_contract_id" TEXT REFERENCES T01(contract_id));
CREATE TABLE "T02"("rule_id" TEXT,"contract_type" TEXT,"calculation_method" TEXT,"unit_fee_yuan" REAL,"room_count_basis" TEXT,"unit_fee_basis" TEXT,"review_status" TEXT,"confirmed_by" TEXT,"confirmed_at" TEXT,"result_location" TEXT,"rule_note" TEXT);
CREATE TABLE "T02-1"("contract_version_id" TEXT,"rule_version" TEXT,"fee_type" TEXT,"rate_basis_points" INTEGER,"contract_phase" TEXT,"source_start_date" TEXT,"source_end_date" TEXT,"proposed_start_date" TEXT,"company_rate" REAL,"owner_rate" REAL,"building_id" TEXT,"source_sheet" TEXT,"source_row" INTEGER,"source_row_id" TEXT,"review_status" TEXT,"execution_condition" TEXT,"contract_id" TEXT,"remark" TEXT);
CREATE TABLE "T03"("building_sk" TEXT,"building_id" TEXT UNIQUE,"project_id" TEXT,"project" TEXT,"building" TEXT,"effective_date" TEXT,"expiry_date" TEXT,"is_current" INTEGER,"data_status" TEXT,"source_row_id" TEXT,"created_at" TEXT);
CREATE TABLE contract_link(legacy_obs_row INTEGER,new_contract_id TEXT REFERENCES T01(contract_id),original_number TEXT,original_name TEXT,match_rule TEXT,PRIMARY KEY(legacy_obs_row,new_contract_id));
CREATE TABLE contract_building(contract_id TEXT REFERENCES T01(contract_id),building_id TEXT,source_rule TEXT,PRIMARY KEY(contract_id,building_id));
CREATE TABLE contract_id_registry(contract_id TEXT PRIMARY KEY REFERENCES T01(contract_id),identity_json TEXT);
CREATE TABLE rate_contract_link(rate_source_row INTEGER,contract_id TEXT REFERENCES T01(contract_id),PRIMARY KEY(rate_source_row,contract_id));
CREATE TABLE building_alias(from_building_id TEXT,to_building_id TEXT,PRIMARY KEY(from_building_id,to_building_id));
CREATE TABLE building_scope(building_id TEXT PRIMARY KEY,project TEXT,building TEXT,canonical_building_id TEXT);
CREATE TABLE month_calendar(month_index INTEGER PRIMARY KEY,month_id TEXT UNIQUE,month_start TEXT,month_end TEXT);
CREATE TABLE T04_period(building_id TEXT REFERENCES building_scope(building_id),from_month_index INTEGER,to_month_index INTEGER,contract_id TEXT REFERENCES T01(contract_id),candidate_count INTEGER,remark TEXT);
CREATE INDEX T04_period_span ON T04_period(building_id,from_month_index,to_month_index);
CREATE TABLE historical_execution_reference(building_id TEXT,month_id TEXT,source_contract_id TEXT,new_contract_ids TEXT,reference_note TEXT,PRIMARY KEY(building_id,month_id));
CREATE TABLE contract_use_scope(contract_id TEXT REFERENCES T01(contract_id),use_scope TEXT,note TEXT);
CREATE TABLE current_issue(table_name TEXT,record_ref TEXT,issue TEXT,note TEXT);
CREATE TABLE field_description(table_name TEXT,column_order INTEGER,field_name TEXT,description TEXT,PRIMARY KEY(table_name,column_order));
CREATE TABLE current_source_audit(source_kind TEXT,record_ref TEXT,details_json TEXT);
CREATE TABLE import_batch(version TEXT PRIMARY KEY,source_workbook TEXT,source_sha256 TEXT,record_count INTEGER,scope TEXT);
CREATE TABLE source_archive(source_file TEXT,source_sheet TEXT,source_row INTEGER,raw_json TEXT,PRIMARY KEY(source_file,source_sheet,source_row));
CREATE TABLE record_lineage(obs_row INTEGER,source_file TEXT,source_sheet TEXT,source_row INTEGER,role TEXT,details_json TEXT);
CREATE TABLE change_log(obs_row INTEGER,field_name TEXT,old_json TEXT,new_json TEXT);
CREATE VIEW T04 AS
 SELECT n.contract_id,n.contract_name,o.contract_number,o.contract_name AS original_contract_name,
 b.project,b.building,p.building_id,n.contract_start_date,n.contract_end_date,o.signed_date,n.effective_date,
 m.month_id,p.remark
 || CASE WHEN m.month_id='999912' AND p.contract_id IS NULL THEN '；末月无合同，核对后续签约，当前按已确认期外留空' ELSE '' END
 || CASE WHEN p.candidate_count>1 AND e.new_contract_ids<>'' THEN '；V16已确认执行参考：'||e.new_contract_ids||'（原批次结论保留）' ELSE '' END
 || CASE WHEN p.contract_id IS NULL AND EXISTS(SELECT 1 FROM contract_building cb JOIN contract_use_scope u ON u.contract_id=cb.contract_id WHERE cb.building_id=p.building_id) THEN '；存在仅供特殊付款合同，不作为普通月账默认执行' ELSE '' END AS remark
 FROM month_calendar m JOIN T04_period p ON m.month_index BETWEEN p.from_month_index AND p.to_month_index
 JOIN building_scope b ON b.building_id=p.building_id
 LEFT JOIN T01 n ON n.contract_id=p.contract_id
 LEFT JOIN "T01-1" o ON o.new_contract_id=p.contract_id
 LEFT JOIN historical_execution_reference e ON e.building_id=b.canonical_building_id AND e.month_id=m.month_id;
