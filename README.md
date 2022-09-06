# okta-to-humio

This utility can be used to fetch Okta Audit API Events and send them to Humio. The collection is based on pulling events from Okta, an alternative is to use Okta's new push based audit logging.

This document describes how to configure the AWS Lambda based collection between Okta and Humio, or how to [configure with the Humio Log Collector](#humio-log-collector-configuration).

## AWS Lambda
**Prerequisites:**

* Create an Okta “Admin - Read Only” token as described [here](https://developer.okta.com/docs/api/getting_started/getting_a_token)

NOTE: Significant portions of this guide are taken from the AWS Samples, [here](https://github.com/aws-samples/aws-serverless-workshops/tree/master/WebApplication/3_ServerlessBackend) on GitHub. This lambda function was originally developed [here](https://github.com/SumoLogic/sumologic-content/tree/master/Okta) under the Apache 2.0 license, and subsequently copied here.

### 1. Create an Amazon DynamoDB Table
We will use the Amazon DynamoDB console to create a new DynamoDB table. Call your table `okta-to-humio` and give it a partition key called `okta_org_url` with type `String`. The table name and partition key are case sensitive. Make sure you use the exact IDs provided. Use the defaults for all other settings.

After you've created the table, note the ARN for use in the next step.

Step-by-step instructions:

1. From the AWS Management Console, choose **Services** then select **DynamoDB** under Databases.
1. Choose **Create table**.
1. Enter `okta-to-humio` for the Table name. This field is case sensitive.
1. Enter `okta_org_url` for the Partition key and select `String` for the key type. This field is case sensitive.
1. Check the `Use default settings` box and choose **Create**.
1. Scroll to the bottom of the Overview section of your new table and note the ARN. You will use this in the next section.


### 2. Create an IAM Role for Your Lambda function
Next we use the IAM console to create a new role. Name it `OktaToHumioLambda` and select AWS Lambda for the role type. You'll need to attach policies that grant your function permissions to write to Amazon CloudWatch Logs and put items to your DynamoDB table.

Attach the managed policy called `AWSLambdaBasicExecutionRole` to this role to grant the necessary CloudWatch Logs permissions. Also, create a custom inline policy for your role that allows the `ddb:PutItem` action for the table you created in the previous section.

Step-by-step instructions:

1. From the AWS Management Console, click on **Services** and then select **IAM** in the Security, Identity & Compliance section.
1. Select **Roles** in the left navigation bar and then choose **Create role**.
1. Select **Lambda** for the role type from the AWS service group, then click **Next: Permissions**
1. Begin typing `AWSLambdaBasicExecutionRole` in the Filter text box and check the box next to that role.
1. Click **Next: Review**.
1. Enter `OktaToHumioLambda` for the Role name.
1. Choose **Create role**.
1. Type `OktaToHumioLambda` into the filter box on the Roles page and choose the role you just created.
1. On the Permissions tab, choose the **Add inline policy** link in the lower right corner to create a new inline policy. 
1. Select **Choose a service**.
1. Begin typing `DynamoDB` into the search box labeled Find a service and select **DynamoDB** when it appears.
1. Choose **Select actions**.
1. Begin typing `PutItem` into the search box labeled Filter actions and check the box next to **PutItem** when it appears.
1. Repeat step 13 for **GetItem** 
1. Select the **Resources** section.
1. With the Specific option selected, choose the **Add ARN** link in the table section.
1. Paste the ARN of the table you created in the previous section in the **Specify ARN for table** field, and choose **Add**.
1. Choose **Review Policy**.
1. Enter `DynamoDBReadWriteAccess` for the policy name and choose **Create policy**.


### 3. Create a Lambda Function for Sending Logs

Use the AWS Lambda console to create a new Lambda function called `OktaToHumio` that will run as a scheduled task to send the logs.

Make sure to configure your function to use the `OktaToHumioLambda` IAM role you created in the previous section.

Step-by-step instructions:

1. Choose on **Services** then select **Lambda** in the Compute section.
1. Click **Create function**.
1. Keep the default “Author from scratch” card selected.
1. Enter `OktaToHumioLambda` in the Name field.
1. Select **Python 3.8** for the Runtime.
1. Ensure **Choose an existing role** is selected from the Role dropdown.
1. Select **OktaToHumioLambda** from the Existing Role dropdown.
1. Click on **Create function**.

### 4. Setup the Trigger

1. Under **Add Triggers** select **CloudWatch Events**
1. Select the new trigger and under **Configure Triggers** select **Create a new rule**
1. Enter `every_5_minutes` as the rule name
1. Enter `Every 5 Minutes` as the rule description
1. Choose **Schedule expression**
1. For the rate enter `rate(5 minutes)`
1. **Enable Trigger**
1. Click **Add**

### 5. Copy the Code

1. Select the lambda function
1. In the code editor("Code source"), copy the contents of `okta-logs-to-humio.py` from the repo into the editor
1. **Click Save**
1. Make sure the python script in the lambda is named `okta-logs-to-humio.py` (note: default is `lambda_function.py`)

### 6. Setup the Environment Variables

1. Under "Configuration" -> “Environment Variables”, add:

	| Variable Name | Example Value | Description |
	|---|---|---|
	| `DDB_TABLE` | `okta-to-humio` | The DynamoDB table name |
	| `HUMIO_SERVER` | `https://cloud.humio.com/` or `https://cloud.community.humio.com/` | URL for Humio instance |
	| `HUMIO_TOKEN` | `ebe59567-74eb-4b3c-8949-017450515612` | Ingest token from Humio |
	| `OKTA_ORG_URL` | `https://myorg.okta.com` | The URL of your Okta instance |
	| `OKTA_API_KEY` | `00XXXXX_wjkbJksue789s7s99d-0QrGh3jj12rAQ` | API key generated for Okta Access |


1. Under "Configuration" -> "General configuration" -> “Basic Settings”, configure the timeout for the function to two (2) minutes.

1. Under "Code" -> "Runtime settings" area, set the handler to `okta-logs-to-humio.lambda_handler`

1. Under "Code" -> "Code source" area, go to "File" and click **Save All**. Click the "Deploy" dialog box to finalize the function.

NOTE: Data will be transferred once the first scheduled execution of the function takes place, or you can run a `Test` of the function with any/default test event payload.


# Humio Log Collector Configuration

Using the same configug examples as above the following cmd/exec input can be used with the Humio Log Collector to collect the events. Note that this is using the okta-audit-export.py script. It assumes that you have placed the `okta-audit-export.py` file in the folder `/root/okta-to-humio/`.

/!\ Be sure to check that the user the Humio Log Collector will run as has permissions to execute the command from that location.

/!\ Make sure that the interval is greater than the timeout configured for the collection script.

The Humio Log Collector source config:
```yaml
sources:
  okta_export:
    type: cmd
    cmd: /usr/bin/python3
    mode: scheduled
    args:
      - /root/okta-to-humio/okta-audit-export.py
      - /root/okta-to-humio/config.json
    interval: 300
    sink: humio
```


Create a parser for the Okta events with the following content:

```
@collect.stream match {
  stdout => parseJson() | parseTimestamp(field="published") ;
  stderr => @timestamp := @ingesttimestamp ;
}
```

