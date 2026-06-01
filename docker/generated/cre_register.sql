-- Generated from cre_profile.example.yaml
USE ROLE ACCOUNTADMIN;

CREATE OR REPLACE CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_r
    IMAGE_PATH = '/mydb/myschema/my_cre_images/sfnb-multilang-r:v2'
    BASE_IMAGE_TYPE = CPU;

DESCRIBE CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_r;

GRANT USAGE ON CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_r TO ROLE SYSADMIN;
-- GRANT USAGE ON CUSTOM RUNTIME ENVIRONMENT sfnb_multilang_r TO ROLE <notebook_role>;
