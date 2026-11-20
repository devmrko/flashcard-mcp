-- FLASHCARD_DB=oracle only. Run ONCE as ADMIN on your ADB (Database Actions SQL worksheet or sqlplus).
-- Replace the password before running. Do not commit the real one.
CREATE USER flashcard IDENTIFIED BY "<CHANGE_ME_Strong#Passw0rd>";
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO flashcard;
ALTER USER flashcard QUOTA 200M ON DATA;
-- Tables are created by the app: `uv run flashcard init-db` (idempotent).
