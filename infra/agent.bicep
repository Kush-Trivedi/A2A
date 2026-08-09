// One team agent = one Container App with its OWN scale rules (noisy-neighbor
// isolation) inside the shared environment. Instantiated once per agent.
param name string
param environmentId string
param image string
param targetPort int
param teamKey string
param cpu string = '0.5'
param memory string = '1Gi'
param minReplicas int = 0   // scale-to-zero for idle agents
param maxReplicas int = 3
param envName string = 'dev'

resource agentApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: resourceGroup().location
  tags: { team: teamKey, platform: 'ace' }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      // Internal-only ingress: reachable as https://<name>.internal.<env-domain>
      // — this DNS name is what the team registers as card_url. No ports.
      ingress: { external: false, targetPort: targetPort, transport: 'auto' }
    }
    template: {
      containers: [
        {
          name: name
          image: image
          resources: { cpu: json(cpu), memory: memory }
          env: [ { name: 'ENV', value: envName } ]
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas }
    }
  }
}

output fqdn string = agentApp.properties.configuration.ingress.fqdn
