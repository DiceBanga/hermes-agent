import { describe, expect, it } from 'vitest'

import { mcpTargets } from './connector-tools'

describe('mcpTargets', () => {
  it('preserves explicit private install_custom provenance as a distinct action', () => {
    expect(mcpTargets('manage_connections', { action: 'install_custom', connectors: [{ mcp: true, name: 'Local' }] }))
      .toEqual([{ action: 'install_custom', name: 'local' }])
  })
})
