CREATE TABLE "T01" (
 "contract_number" TEXT,
 "contract_name" TEXT,
 "项目名" TEXT,
 "楼栋名" TEXT,
 "contract_start_date" TEXT,
 "contract_end_date" TEXT,
 "signed_date" TEXT,
 "effective_date" TEXT,
 "expiry_date" TEXT,
 "is_current" INTEGER,
 "data_status" TEXT,
 "source_row_id" TEXT,
 "fee_rule_version" TEXT,
 "contract_type" TEXT,
 "payee_name" TEXT,
 "bank_name" TEXT,
 "bank_account_ref" TEXT,
 "contract_profile_source_row_id" TEXT,
 "bank_account_number" TEXT,
 "bank_verification_status" TEXT,
 "contract_start_month_id" TEXT,
 "contract_end_month_id" TEXT
);
CREATE TABLE import_batch(version TEXT PRIMARY KEY,source_workbook TEXT,source_sha256 TEXT,record_count INTEGER,scope TEXT);
CREATE TABLE field_description(column_order INTEGER PRIMARY KEY,field_name TEXT,description TEXT);
CREATE TABLE source_archive(source_file TEXT,source_sheet TEXT,source_row INTEGER,raw_json TEXT,PRIMARY KEY(source_file,source_sheet,source_row));
CREATE TABLE record_lineage(obs_row INTEGER,source_file TEXT,source_sheet TEXT,source_row INTEGER,role TEXT,details_json TEXT);
CREATE TABLE change_log(obs_row INTEGER,field_name TEXT,old_json TEXT,new_json TEXT);
