package io.airbyte.cdk.load.orchestration

interface DestinationHandler {
    fun execute(sql: Sql)

    /**
     * Create the namespaces (typically something like `create schema`).
     *
     * This function should assume that all `namespaces` are valid identifiers, i.e. any special
     * characters have already been escaped, they respect identifier name length, etc.
     */
    fun createNamespaces(namespaces: List<String>)
}
