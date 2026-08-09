// ACE platform on Azure Container Apps: ONE shared environment, one app per
// service. Deploy: az deployment group create -g <rg> -f main.bicep \
//   -p registry=<acr>.azurecr.io envName=dev
param envName string = 'dev'
param registry string
param location string = resourceGroup().location

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'ace-logs-${envName}'
  location: location
  properties: { sku: { name: 'PerGB2018' } }
}

// Shared Container Apps Environment — one network, one log sink, N apps.
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'ace-env-${envName}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// ACE control plane (the only externally reachable app besides the frontend).
resource ace 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ace-control-plane'
  location: location
  tags: { team: 'platform', platform: 'ace' }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: { external: true, targetPort: 3000, transport: 'auto' }
    }
    template: {
      containers: [
        {
          name: 'ace'
          image: '${registry}/ace/control-plane:latest'
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [ { name: 'ENV', value: envName } ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
}

// One module call per team agent — teams own their cpu/memory/replica knobs.
var agents = [
  { name: 'scheduling',    team: 'clinical_care', port: 3100 }
  { name: 'insurance',     team: 'pay_ops',       port: 3200 }
  { name: 'general',       team: 'ace_platform',  port: 3300 }
  { name: 'file-qa',       team: 'ace_platform',  port: 3400 }
  { name: 'sharepoint-qa', team: 'clinical_care', port: 3500 }
  { name: 'blob-qa',       team: 'pay_ops',       port: 3600 }
  { name: 'sms-outreach',  team: 'clinical_care', port: 3700 }
]

module agentApps 'agent.bicep' = [for a in agents: {
  name: 'agent-${a.name}'
  params: {
    name: a.name
    environmentId: cae.id
    image: '${registry}/ace/${a.name}-agent:latest'
    targetPort: a.port
    teamKey: a.team
    envName: envName
  }
}]

output aceUrl string = ace.properties.configuration.ingress.fqdn
output agentFqdns array = [for (a, i) in agents: agentApps[i].outputs.fqdn]
