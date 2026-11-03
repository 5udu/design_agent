def project_facts(project):
    """Project facts shared by the agent context and the read-project tool."""
    return {key: project[key] for key in ['id', 'name', 'brand', 'product', 'approved_copy', 'current_image_id'] if project.get(key)}
