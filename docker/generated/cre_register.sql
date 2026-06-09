-- Generated from sfnb_ml.example.yaml
USE ROLE ACCOUNTADMIN;

CREATE OR REPLACE CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_ml
    IMAGE_PATH = '/mydb/myschema/my_cre_images/sfnb-multilang-r:ml-v1'
    BASE_IMAGE_TYPE = CPU;

DESCRIBE CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_ml;

GRANT USAGE ON CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_ml TO ROLE SYSADMIN;
-- GRANT USAGE ON CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_ml TO ROLE <notebook_role>;
