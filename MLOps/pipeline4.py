import kfp
import kfp.components as comp
from kfp import dsl
import os
import time
from kubernetes import client as k8s_client
from kfp import onprem


path = "/code"

prep_type = ["normalization", "standard", "minmax"]
clf_type = ["logistic", "svm", "randomforest"]

def get_randomId(): #randomID 반환
    import string
    import random
    _LENGTH = 14  # 14자리
    string_pool = string.ascii_letters # 대소문자
    result = "" # 결과 값
    for i in range(_LENGTH) :
        result += random.choice(string_pool) # 랜덤한 문자열 하나 선택 print(result)
    return result

def is_method(method): #method에 따른 type 출력
    if method in prep_type:
        return "prep"
    elif method in clf_type:
        return "clf"
    else:
        return None

def get_clf_image(clf_type): # classification에 해당하는 image 출력
    if clf_type == "logistic":
        return "kjoohyu/cls_logistic:0.116"
    elif clf_type == "svm":
        return "kjoohyu/cls_svm:0.112"
    elif clf_type == "randomforest":
        return "kjoohyu/cls_randomforest:0.112"
    else:
        return None

def get_prep_image(prep_type): # preprocessing에 해당하는 image 출력
    if prep_type == "normalization":
        return "kjoohyu/prep_scaler_norm:0.12"
    elif prep_type == "standard":
        return "kjoohyu/prep_scaler_strd:0.12"
    elif prep_type == "minmax":
        return "kjoohyu/prep_scaler_minmax:0.12"
    else:
        return None

def set_config(arguments): # create_run_from_pipeline_func()의 arguments로 인자를 보내면 출력이 안되므로 글로벌 변수를 사용
    global data
    global split_method
    global prep_method
    global cls_method
    global gcp_info

    data = arguments["data"]
    split_method = arguments["split_method"]
    prep_method = arguments["prep_method"]
    cls_method = arguments["cls_method"]
    gcp_info = arguments["gcp_info"]

@dsl.pipeline( # pipeline 설명
    name='iris',
    description='iris example with kubeflow'
)
def sample_pipeline():

    dsl.get_pipeline_conf().set_parallelism(4)

    select_data = dsl.ContainerOp( # data select 부분
        name="selected data",
        image="kjoohyu/load_data:0.116",
        arguments=[
            '--selected_data', data
        ],
        file_outputs={ 'data_file' : '/data.csv'}
    )

    split_train_test = dsl.ContainerOp( # train, test 데이터 분리
        name="split train test data",
        image="kjoohyu/split_train_test:0.166",
        arguments=[
            '--data_file', dsl.InputArgumentPath(select_data.outputs['data_file']),
            '--split_method', split_method
        ],
        file_outputs={'X_train': '/X_train.csv',
                      'X_test': '/X_test.csv',
                      'Y_train': '/Y_train.csv',
                      'Y_test': '/Y_test.csv',
                      }
    )

    # 여기부터 동적으로 파이프라인 설정함
   ridArr = []
    clf_pipes = []
    clf_output = []
    for i in range(len(prep_method)):
        if prep_method[i]['prev']:
            continue
        rid = get_randomId()
        ridArr.append(rid)

        prep_image = get_prep_image(prep_method[i]["method"])
        if prep_image == None:
            continue # will create None prep

        p = dsl.ContainerOp(
            name=f"preprocessing-scaler-{prep_method[i]['method']}-{rid}",
            image=prep_image,
            arguments=[
                '--split_X_train', dsl.InputArgumentPath(split_train_test.outputs['X_train']),
                '--split_X_test', dsl.InputArgumentPath(split_train_test.outputs['X_test']),
                '--prep_method', prep_method[i]
            ],
            file_outputs={'X_train': '/X_train.csv',
                          'X_test': '/X_test.csv'}
        )

        previous_method = eval(prep_method[i]['next'])
        m = is_method(previous_method['method'])

        while(m):
            if m == "prep":
                prep_image = get_prep_image(previous_method["method"])

                v = dsl.ContainerOp(
                    name=f"preprocessing-scaler-{previous_method['method']}-{rid}" ,
                    image=prep_image,
                    arguments=[
                        '--split_X_train', dsl.InputArgumentPath(p.outputs['X_train']),
                        '--split_X_test', dsl.InputArgumentPath(p.outputs['X_test']),
                        '--prep_method', previous_method
                    ],
                    file_outputs={'X_train': '/X_train.csv',
                                  'X_test': '/X_test.csv'}
                ).after(p)

            elif m == "clf":
                clf_image = get_clf_image(previous_method["method"])

                v = dsl.ContainerOp(
                    name=f"classification-{previous_method['method']}-{rid}" ,
                    image=clf_image,
                    arguments=[
                        '--classification_method', previous_method,
                        '--fileId', rid,
                        '--X_train', dsl.InputArgumentPath(p.outputs['X_train']),
                        '--X_test', dsl.InputArgumentPath(p.outputs['X_test']),
                        '--Y_train', dsl.InputArgumentPath(split_train_test.outputs['Y_train']),
                        '--Y_test', dsl.InputArgumentPath(split_train_test.outputs['Y_test'])
                    ],
                    #pvolumes={'/': evaluation.pvolume}
                    file_outputs={'result': f'/{rid}.csv'}
                ).after(p)

                s = dsl.ContainerOp(
                    name=f"save-result-{rid}",
                    image="kjoohyu/save_result:0.112",
                    arguments=[
                        '--fileId', rid,
                        '--bucket', gcp_info["bucketId"],
                        '--kfp_name', gcp_info["kfpName"],
                        '--gcp_folder_path', gcp_info["gcp_folder"],
                        '--result_file', dsl.InputArgumentPath(v.outputs['result'])
                    ],
                ).after(v)
                break
            else:
                break
            p = v

            previous_method = eval(previous_method['next'])
            m = is_method(previous_method['method'])



    #########################################################################################


    #evaluation.after(classification)
    
if __name__ == "__main__":

    data_type = { "data" : "iris", "label" : None}
    split = []
    split.append({"size" : 0.7, "shuffle": True, "random_state": 11})

    #prep - method(normalization, standard davigation, minmax scaler), prev(이전에 사용한 컨테이너), next(다음에 사용할 컨테이너)
    prep = []
    norm = {"method":"normalization", "norm":"l2", "prev": None, "next":"cls_method[0]"}
    norm2 = {"method": "normalization", "norm": "l2", "prev": None, "next": "prep_method[4]"}
    strd = {"method":"standard", "prev": None, "next":"cls_method[0]"}
    minmax = {"method": "minmax", "prev" : None, "next":"cls_method[1]"}
    minmax2 = {"method": "minmax", "prev": "prep_method[1]", "next": "cls_method[1]"}

    prep.append(norm)
    prep.append(norm2)
    prep.append(strd)
    prep.append(minmax)
    prep.append(minmax2)

    #classification - method(logistic, svm, randomforest), arguments
    classification = []
    logistic = {"method": "logistic", "random_state":11, "next":None}
    svm = {"method": "svm", "kernel": "linear", "degree" : 3, "random_state": 11, "next":None}
    randomforest = {"method": "randomforest", "n_estimators": 50, "max_depth":15, "min_samples_split":3, "min_samples_leaf": 2, "random_state": 11, "next":None}

    classification.append(logistic)
    classification.append(svm)
    classification.append(randomforest)

    ###################################################################
    gcp_info = {"bucketId" : "ess-bucket-1", "kfpName" : get_randomId(), "gcp_folder": "kfp-result/"}
    ##################################################################

    arguments = {}
    arguments['data'] = data_type
    arguments['split_method'] = split
    arguments['prep_method'] = prep
    arguments['cls_method'] = classification
    arguments['gcp_info'] = gcp_info

    set_config(arguments) # 전역변수로 설정

    t = time.localtime()

    experiments_name = "sample-%04d%02d%02d_%02d%02d%02d" %(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)

    # os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "./keti-iisrc.example.json"
    #client = kfp.Client(host='https://23fbddd5362afcb7-dot-asia-east1.pipelines.googleusercontent.com')
    # token =
    # http:32553, https:32003
    client = kfp.Client(host='host-ip')
    #my_experiment = client.create_experiment(name='Basic Experiment')
    #print(client.set_user_namespace(False))
    my_run = client.create_run_from_pipeline_func(sample_pipeline, arguments={})

    #client.wait_for_run_completion(my_run.run_id, 150)
