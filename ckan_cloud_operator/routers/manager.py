import yaml
import hashlib

from ckan_cloud_operator import kubectl
from ckan_cloud_operator.routers.annotations import CkanRoutersAnnotations
from ckan_cloud_operator.routers.traefik import manager as traefik_manager
from ckan_cloud_operator.routers.routes import manager as routes_manager
from ckan_cloud_operator.infra import CkanInfra


ROUTER_TYPES = {
    'traefik': {
        'default': True,
        'manager': traefik_manager
    }
}

DEFAULT_ROUTER_TYPE = [k for k,v in ROUTER_TYPES.items() if v.get('default')][0]


def create(router_name, router_spec):
    router_type = router_spec.get('type')
    default_root_domain = router_spec.get('default-root-domain')
    assert router_type in ROUTER_TYPES and default_root_domain, f'Invalid router spec: {router_spec}'
    print(f'Creating CkanCloudRouter {router_name} {router_spec}')
    labels = _get_labels(router_name, router_type)
    router = kubectl.get_resource('stable.viderum.com/v1', 'CkanCloudRouter', router_name, labels,
                                  spec=dict(router_spec, **{'type': router_type}))
    router_manager = ROUTER_TYPES[router_type]['manager']
    router = router_manager.create(router)
    annotations = CkanRoutersAnnotations(router_name, router)
    annotations.json_annotate('default-root-domain', default_root_domain)


def get_traefik_router_spec(default_root_domain=None, cloudflare_email=None, cloudflare_api_key=None):
    if not default_root_domain: default_root_domain = 'default'
    if not cloudflare_email: cloudflare_email = 'default'
    if not cloudflare_api_key: cloudflare_api_key = 'default'
    return {
        'type': 'traefik',
        'default-root-domain': default_root_domain,
        # the cloudflare spec is not saved as part of the CkanCloudRouter spec
        # it is removed and saved as a secret by the traefik router manager
        'cloudflare': {
            'email': cloudflare_email,
            'api-key': cloudflare_api_key
        }
    }


def update(router_name, wait_ready=False):
    router, spec, router_type, annotations, labels, router_type_config = _init_router(router_name)
    print(f'Updating CkanCloudRouter {router_name} (type={router_type})')
    routes = routes_manager.list(labels)
    router_type_config['manager'].update(router_name, wait_ready, spec, annotations, routes)


def list(full=False, values_only=False, async_print=True):
    res = None if async_print else []
    for router in kubectl.get('CkanCloudRouter')['items']:
        if values_only:
            data = {'name': router['metadata']['name'],
                    'type': router['spec']['type']}
        else:
            data = get(router)
            if not full:
                data = {'name': data['name'],
                        'type': data['type'],
                        'ready': data['ready']}
        if res is None:
            print(yaml.dump([data], default_flow_style=False))
        else:
            res.append(data)
    if res is not None:
        return res


def get(router_name_or_values):
    if type(router_name_or_values) == str:
        router_name = router_name_or_values
        router_values = kubectl.get(f'CkanCloudRouter {router_name}')
    else:
        router_name = router_name_or_values['metadata']['name']
        router_values = router_name_or_values
    router, spec, router_type, annotations, labels, router_type_config = _init_router(router_name, router_values)
    deployment_data = router_type_config['manager'].get(router_name, 'deployment')
    routes = routes_manager.list(_get_labels(router_name, router_type))
    return {'name': router_name,
            'annotations': router_values['metadata']['annotations'],
            'routes': [route.get('spec') for route in routes] if routes else [],
            'type': router_type,
            'deployment': deployment_data,
            'ready': deployment_data.get('ready', False)}


def create_subdomain_route(router_name, route_spec):
    target_type = route_spec['target-type']
    sub_domain = route_spec.get('sub-domain')
    root_domain = route_spec.get('root-domain')
    ckan_infra = CkanInfra()
    if target_type == 'datapusher':
        target_resource_id = route_spec['datapusher-name']
    elif target_type == 'deis-instance':
        target_resource_id = route_spec['deis-instance-id']
    elif target_type == 'backend-url':
        target_resource_id = route_spec['target-resource-id']
    else:
        raise Exception(f'Invalid route spec: {route_spec}')
    if not sub_domain or sub_domain == 'default':
        assert ckan_infra.ROUTERS_ENV_ID, 'missing ckan infra ROUTERS_ENV_ID value'
        sub_domain = f'cc-{ckan_infra.ROUTERS_ENV_ID}-{target_resource_id}'
        print(f'Using default sub domain: {sub_domain}')
    if not root_domain or root_domain == 'default':
        root_domain = 'default'
        print('root domain will be determined in run-time on router update')
    route_name = 'cc' + hashlib.sha3_224(f'{target_type} {target_resource_id} {root_domain} {sub_domain}'.encode()).hexdigest()
    router, spec, router_type, annotations, labels, router_type_config = _init_router(router_name)
    route_type = f'{target_type}-subdomain'
    labels.update(**{
        'ckan-cloud/route-type': route_type,
        'ckan-cloud/route-root-domain': root_domain,
        'ckan-cloud/route-sub-domain': sub_domain,
        'ckan-cloud/route-target-type': target_type,
        'ckan-cloud/route-target-resource-id': target_resource_id,
    })
    spec = {
        'name': route_name,
        'type': route_type,
        'root-domain': root_domain,
        'sub-domain': sub_domain,
        'router_name': router_name,
        'router_type': router_type,
        'route-target-type': target_type,
        'route-target-resource-id': target_resource_id,
    }
    if target_type == 'datapusher':
        labels['ckan-cloud/route-datapusher-name'] = spec['datapusher-name'] = route_spec['datapusher-name']
    elif target_type == 'deis-instance':
        labels['ckan-cloud/route-deis-instance-id'] = spec['deis-instance-id'] = route_spec['deis-instance-id']
    elif target_type == 'backend-url':
        spec['backend-url'] = route_spec['backend-url']
    route = kubectl.get_resource('stable.viderum.com/v1', 'CkanCloudRoute', route_name, labels, spec=spec)
    kubectl.create(route)


def install_crds():
    """Ensures installaion of the custom resource definitions on the cluster"""
    kubectl.install_crd('ckancloudrouters', 'ckancloudrouter', 'CkanCloudRouter')
    routes_manager.install_crds()


def delete(router_name, router_type=None):
    if router_type:
        router_type_config = ROUTER_TYPES[router_type]
    else:
        router, spec, router_type, annotations, labels, router_type_config = _init_router(router_name)
    router_type_config['manager'].delete(router_name)


def get_datapusher_routes(datapusher_name):
    labels = {'ckan-cloud/route-datapusher-name': datapusher_name}
    return kubectl.get_items_by_labels('CkanCloudRoute', labels, required=False)


def get_backend_url_routes(target_resorce_id):
    labels = {'ckan-cloud/route-target-resource-id': target_resorce_id}
    return kubectl.get_items_by_labels('CkanCloudRoute', labels, required=False)


def get_deis_instance_routes(deis_instance_id):
    labels = {'ckan-cloud/route-deis-instance-id': deis_instance_id}
    return kubectl.get_items_by_labels('CkanCloudRoute', labels, required=False)


def _get_labels(router_name, router_type):
    return {'ckan-cloud/router-name': router_name, 'ckan-cloud/router-type': router_type}


def _init_router(router_name, router_values=None):
    router = kubectl.get(f'CkanCloudRouter {router_name}') if not router_values else router_values
    spec = router['spec']
    router_type = spec['type']
    assert router_type in ROUTER_TYPES, f'Unsupported router type: {router_type}'
    router_type_config = ROUTER_TYPES[router_type]
    annotations = CkanRoutersAnnotations(router_name, router)
    labels = _get_labels(router_name, router_type)
    return router, spec, router_type, annotations, labels, router_type_config
