 foreach ($file in @("CAvideos.csv", "CA_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=ca/
>> }

 foreach ($file in @("INvideos.csv", "IN_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=in/
>> }

 foreach ($file in @("KRvideos.csv", "KR_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=KR/
>> }

foreach ($file in @("MXvideos.csv", "MX_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=MX/
>> }

 foreach ($file in @("USvideos.csv", "US_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=US/
>> }

foreach ($file in @("RUvideos.csv", "RU_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=RU/
>> }

foreach ($file in @("JPvideos.csv", "JP_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=JP/
>> }

 foreach ($file in @("DEvideos.csv", "DE_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=DE/
>> }

 foreach ($file in @("FRvideos.csv", "FR_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=FR/
>> }

foreach ($file in @("GBvideos.csv", "GB_category_id.json")) {
>>     aws s3 cp $file s3://youtube-analytics-data-ap-south-01/bronzeLayer/region=GB/
>> }
