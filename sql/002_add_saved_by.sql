-- Only if fc_card was already created before created_by/updated_by existed (run as FLASHCARD).
-- A fresh `flashcard init-db` already includes these columns.
ALTER TABLE fc_card ADD (created_by VARCHAR2(60 CHAR), updated_by VARCHAR2(60 CHAR));
