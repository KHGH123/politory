-- Parameters: @meeting_ids ARRAY<STRING>
BEGIN TRANSACTION;

DELETE FROM `{target_table}`
WHERE JSON_VALUE(jsonData, '$.meeting_id') IN UNNEST(@meeting_ids);

INSERT INTO `{target_table}` (id, jsonData)
SELECT id, jsonData
FROM `{delta_table}`;

COMMIT TRANSACTION;

