import time

import tensorflow as tf
from config import ModelConfig

from utils import *


class Model(object):

    def __init__(self, config):
        self.config = config
        self.load_data()
        self.vocab_size = len(self.index2token)

        # Initialize tf placeholders
        self._sent_placeholder = tf.placeholder(tf.int32, shape=[self.config.batch_size, None], name='sent_ph')
        self._img_placeholder = tf.placeholder(tf.float32, shape=[self.config.batch_size, self.config.img_dim],
                                               name='img_ph')
        self._targets_placeholder = tf.placeholder(tf.int32, shape=[self.config.batch_size, None], name='targets')
        self._dropout_placeholder = tf.placeholder(tf.float32, name='dropout_placeholder')

        # Set the input layer
        with tf.variable_scope('CNN'):
            W_i = tf.get_variable('W_i', shape=[self.config.img_dim, self.config.embed_dim])
            b_i = tf.get_variable('b_i', shape=[self.config.batch_size, self.config.embed_dim])
            img_input = tf.expand_dims(tf.nn.sigmoid(tf.matmul(self._img_placeholder, W_i) + b_i), 1)
            print('Image:', img_input.get_shape())

        with tf.variable_scope('sent_input'):
            word_embeddings = tf.get_variable('word_embeddings', shape=[self.vocab_size, self.config.embed_dim])
            sent_inputs = tf.nn.embedding_lookup(word_embeddings, self._sent_placeholder)
            print('Sent:', sent_inputs.get_shape())

        with tf.variable_scope('all_input'):
            all_inputs = tf.concat(1, [img_input, sent_inputs])
            print('Combined:', all_inputs.get_shape())

        # Set the LSTM layer
        lstm = tf.nn.rnn_cell.BasicLSTMCell(self.config.hidden_dim, forget_bias=1, input_size=self.config.embed_dim)
        lstm_dropout = tf.nn.rnn_cell.DropoutWrapper(lstm, input_keep_prob=self._dropout_placeholder,
                                                     output_keep_prob=self._dropout_placeholder)
        stacked_lstm = tf.nn.rnn_cell.MultiRNNCell([lstm_dropout] * self.config.layers)
        initial_state = stacked_lstm.zero_state(self.config.batch_size, tf.float32)
        outputs, final_state = tf.nn.dynamic_rnn(stacked_lstm, all_inputs, initial_state=initial_state, scope='LSTM')
        output = tf.reshape(outputs, [-1, self.config.hidden_dim])

        self._final_state = final_state
        print('Outputs (raw):', outputs.get_shape())
        print('Final state:', final_state.get_shape())
        print('Output (reshaped):', output.get_shape())

        # Softmax layer
        with tf.variable_scope('softmax'):
            softmax_w = tf.get_variable('softmax_w', shape=[self.config.hidden_dim, self.vocab_size])
            softmax_b = tf.get_variable('softmax_b', shape=[self.vocab_size])
            logits = tf.matmul(output, softmax_w) + softmax_b
        print('Logits:', logits.get_shape())

        # Predictions
        self.logits = logits
        self._predictions = predictions = tf.argmax(logits, 1)
        print('Predictions:', predictions.get_shape())

        # Minimize Loss
        targets_reshaped = tf.reshape(self._targets_placeholder, [-1])
        print('Targets (raw):', self._targets_placeholder.get_shape())
        print('Targets (reshaped):', targets_reshaped.get_shape())

        with tf.variable_scope('loss'):
            # _targets is [-1, ..., -1] so that the first and last logits are not used
            # these correspond to the image step and the <eos> step
            # see: https://www.tensorflow.org/versions/r0.8/api_docs/python/nn.html#sparse_softmax_cross_entropy_with_logits
            self.loss = loss = tf.reduce_sum(
                tf.nn.sparse_softmax_cross_entropy_with_logits(logits, targets_reshaped, name='ce_loss'))
            print('Loss:', loss.get_shape())

        with tf.variable_scope('optimizer'):
            optimizer = tf.train.AdamOptimizer()
            self.train_op = optimizer.minimize(loss)

    def load_data(self, type='train'):
        if type == 'train':
            with open('data/index2token.pkl', 'r') as f:
                self.index2token = pickle.load(f)
            with open('data/preprocessed_train_captions.pkl', 'r') as f:
                self.train_captions, self.train_caption_id2sentence, self.train_caption_id2image_id = pickle.load(f)
            with open('data/train_image_id2feature.pkl', 'r') as f:
                self.train_image_id2feature = pickle.load(f)

    def run_epoch(self, session, train_op):
        total_steps = sum(1 for x in train_data_iterator(self.train_captions, self.train_caption_id2sentence,
                                                         self.train_caption_id2image_id, self.train_image_id2feature,
                                                         self.config))
        total_loss = []
        if not train_op:
            train_op = tf.no_op()
        start = time.time()

        for step, (sentences, images, targets) in enumerate(
                train_data_iterator(self.train_captions, self.train_caption_id2sentence, self.train_caption_id2image_id,
                                    self.train_image_id2feature, self.config)):

            feed = {self._sent_placeholder: sentences,
                    self._img_placeholder: images,
                    self._targets_placeholder: targets,
                    self._dropout_placeholder: self.config.keep_prob}

            loss, _ = session.run([self.loss, train_op], feed_dict=feed)

            total_loss.append(loss)

            if (step % 50) == 0:
                print('%d/%d: loss = %.2f time elapsed = %d' % (
                step, total_steps, np.mean(total_loss), time.time() - start))

        print('Total time: %ds' % (time.time() - start))

        return total_loss

    def generate_caption(self, session, img_feature, toSample=False):
        dp = 1
        img_template = np.zeros([self.config.batch_size, self.config.img_dim])
        img_template[0, :] = img_feature

        sent_predictions = np.ones([self.config.batch_size, 1]) * 3591

        while sent_predictions[0, -1] != 3339 and (sent_predictions.shape[1] - 1) < 50:
            feed = {self._sent_placeholder: sent_predictions,
                    self._img_placeholder: img_template,
                    self._targets_placeholder: np.ones([self.config.batch_size, 1]),  # dummy variable
                    self._dropout_placeholder: dp}

            idx_next_pred = np.arange(1, self.config.batch_size + 1) * (sent_predictions.shape[1] + 1) - 1

            if toSample:
                logits = session.run(self.logits, feed_dict=feed)
                next_logits = logits[idx_next_pred, :]
                raw_predicted = []
                for row_idx in range(next_logits.shape[0]):
                    idx = sample(next_logits[row_idx, :])
                    raw_predicted.append(idx)
                raw_predicted = np.array(raw_predicted)
            else:
                raw_predicted = session.run(self._predictions, feed_dict=feed)
                raw_predicted = raw_predicted[idx_next_pred]

            next_pred = np.reshape(raw_predicted, (self.config.batch_size, 1))
            sent_predictions = np.concatenate([sent_predictions, next_pred], 1)

        predicted_sentence = ' '.join(self.index2token[idx] for idx in sent_predictions[0, 1:-1])
        return predicted_sentence


def main():
    config = ModelConfig()
    with tf.variable_scope('CNNLSTM') as scope:
        model = Model(config)
    loss_history = []
    all_results_json = {}
    init = tf.initialize_all_variables()
    saver = tf.train.Saver()

    with tf.Session() as session:
        session.run(init)
        for epoch in range(config.max_epochs):
            ## train model
            print(            'Epoch %d' % (epoch + 1)
            total_loss = model.run_epoch(session, model.train_op)
            loss_history.extend(total_loss)
            print(            'Average loss: %.1f' % np.mean(total_loss)

            if not os.path.exists(config.model_name):
                os.mkdir(config.model_name)
            if not os.path.exists('%s/weights' % config.model_name):
                os.mkdir('%s/weights' % config.model_name)
            saver.save(session, '%s/weights/model' % config.model_name, global_step=epoch)

            if not os.path.exists('%s/loss' % config.model_name):
                os.mkdir('%s/loss' % config.model_name)
            pickle.dump(loss_history, open('%s/loss/loss_history.pkl' % (config.model_name), 'w'))

            generate_captions_val(session, model, epoch)
            resFile = '%s/results/val_res_%d.json' % (config.model_name, epoch)


if __name__ == '__main__':
    main()
